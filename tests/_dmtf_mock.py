"""Leichter Mock-Redfish-Server für Tests: lädt einen selbst geschriebenen Baum aus tests/_trees.py
({Redfish-Pfad: JSON-Inhalt}, kein Herstellermaterial) und beantwortet GET/PATCH
wie ein echter BMC – inklusive „404 für unbekannte Ressource", damit ServicesEngine.soft() seinen
Graceful-Skip (None statt False) auch gegen reale Baumstrukturen prüfen kann.

GET/PATCH reichten für die Dienste-Engines (Item 1/2); seit der Benutzerverwaltung (Item 3) kommen POST
(neues Konto/SSH-Key in einer Collection) und DELETE (Konto/SSH-Key löschen) dazu – beide nur für
Collections/Ressourcen, die wirklich im Baum stehen. Ein POST auf SessionService/Sessions bleibt bewusst
404 (dort gibt es kein „Members"), damit RedfishClient.auth_header() automatisch auf Basic Auth zurückfällt –
genau wie bei einem echten BMC ohne brauchbaren SessionService.
"""
import json
import os
import shutil
import ssl
import subprocess
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests._trees import TREES


def load_tree(vendor):
    """Eine tiefe Kopie des Testbaums für diesen Hersteller: {Redfish-Pfad: JSON-Inhalt}."""
    return json.loads(json.dumps(TREES[vendor]))


def _deep_merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v


class DmtfMockServer:
    """Serviert einen Hersteller-Baum per HTTPS auf 127.0.0.1, mit Wegwerf-Zertifikat (braucht openssl)."""

    def __init__(self, vendor):
        self.vendor = vendor
        self.tree = load_tree(vendor)
        # Jede Instanz bekommt eine eigene UUID (und damit ihr eigenes Zertifikats-Vertrauen in trust.py):
        # zwei Tests, die denselben Hersteller-Baum laden, sind sonst für die TOFU-Prüfung "dasselbe Gerät"
        # mit zwei verschiedenen Wegwerf-Zertifikaten -> PinMismatch/Sperre beim zweiten Server.
        root = self.tree.get("/redfish/v1")
        if isinstance(root, dict):
            root["UUID"] = str(uuid.uuid4())
        self.lock = threading.Lock()
        self.requests = []          # (Methode, Pfad) jeder Anfrage, damit Tests „nur GET“ belegen können
        self.allow = {}             # Pfad -> Allow-Header; sonst GET, PATCH (Ressource) bzw. GET, POST (Collection)
        self.httpd = None
        self.port = None

    def start(self):
        openssl = shutil.which("openssl")
        if not openssl:
            raise RuntimeError("openssl fehlt (wird für das Wegwerf-Zertifikat gebraucht)")
        tmp = tempfile.mkdtemp(prefix="bmc-dmtf-mock-")
        crt, key = os.path.join(tmp, "c.pem"), os.path.join(tmp, "k.pem")
        subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", crt,
                        "-days", "2", "-subj", "/CN=bmc-steward-mock", "-addext", "subjectAltName=IP:127.0.0.1"],
                       check=True, capture_output=True, timeout=60)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(crt, key)
        shutil.rmtree(tmp, ignore_errors=True)
        srv = self

        class Handler(BaseHTTPRequestHandler):
            timeout = 20
            server_version = "DMTF-Mock"

            def log_message(self, *a):
                pass

            def _reply(self, code, obj):
                data = json.dumps(obj).encode("utf-8") if obj is not None else b"{}"
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                if code == 200 and self.command == "GET":
                    path = self.path.rstrip("/") or "/"
                    allow = srv.allow.get(path)
                    if allow is None:
                        coll = isinstance(obj, dict) and isinstance(obj.get("Members"), list)
                        allow = "GET, POST" if coll else "GET, PATCH"
                    self.send_header("Allow", allow)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                with srv.lock:
                    srv.requests.append(("GET", self.path))
                    doc = srv.tree.get(self.path.rstrip("/") or "/")
                self._reply(200, doc) if doc is not None else self._reply(404, None)

            def do_PATCH(self):
                with srv.lock:
                    srv.requests.append(("PATCH", self.path))
                size = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(size) if size else b"{}"
                path = self.path.rstrip("/") or "/"
                with srv.lock:
                    doc = srv.tree.get(path)
                    if doc is None:
                        self._reply(404, None)
                        return
                    try:
                        patch = json.loads(body) if body else {}
                    except ValueError:
                        patch = {}
                    _deep_merge(doc, patch)
                self._reply(200, doc)

            def do_POST(self):
                with srv.lock:
                    srv.requests.append(("POST", self.path))
                # Benutzerverwaltung (Item 3): POST auf eine Collection legt eine neue Unterressource an
                # (AccountService/Accounts, oder Accounts/<id>/Keys für SSH-Keys). Alles andere (vor allem
                # SessionService/Sessions) bleibt bewusst 404, damit der Client auf Basic Auth zurückfällt.
                path = self.path.rstrip("/") or "/"
                with srv.lock:
                    coll = srv.tree.get(path)
                    if not isinstance(coll, dict) or not isinstance(coll.get("Members"), list):
                        self._reply(404, None)
                        return
                    size = int(self.headers.get("Content-Length") or 0)
                    body = self.rfile.read(size) if size else b"{}"
                    try:
                        new_doc = json.loads(body) if body else {}
                    except ValueError:
                        new_doc = {}
                    new_id = str(len(coll["Members"]) + 1)
                    new_path = f"{path}/{new_id}"
                    new_doc.setdefault("Id", new_id)
                    new_doc.setdefault("@odata.id", new_path)
                    new_doc.setdefault("Enabled", True)
                    srv.tree[new_path] = new_doc
                    coll["Members"].append({"@odata.id": new_path})
                    if "Members@odata.count" in coll:
                        coll["Members@odata.count"] = len(coll["Members"])
                self.send_response(201)
                self.send_header("Location", new_path)
                data = json.dumps(new_doc).encode("utf-8")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_DELETE(self):
                with srv.lock:
                    srv.requests.append(("DELETE", self.path))
                # Löscht eine einzelne Ressource (Konto oder SSH-Key) und entfernt sie aus der Collection,
                # in der sie als Member stand.
                path = self.path.rstrip("/") or "/"
                with srv.lock:
                    if path not in srv.tree:
                        self._reply(404, None)
                        return
                    del srv.tree[path]
                    for doc in srv.tree.values():
                        members = doc.get("Members") if isinstance(doc, dict) else None
                        if isinstance(members, list):
                            before = len(members)
                            members[:] = [m for m in members
                                         if not (isinstance(m, dict) and m.get("@odata.id") == path)]
                            if len(members) != before and "Members@odata.count" in doc:
                                doc["Members@odata.count"] = len(members)
                self._reply(204, None)

        for port in (0,):
            self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.port = self.httpd.server_address[1]
        self.httpd.daemon_threads = True
        self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True, do_handshake_on_connect=False)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    @property
    def addr(self):
        return f"127.0.0.1:{self.port}"

    def resource(self, path):
        with self.lock:
            return self.tree.get(path.rstrip("/") or "/")

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
