"""Zertifikate des virtuellen BMC: PEM prüfen und lesen, Zertifikatsanforderung (CSR) erzeugen.

Benutzt das Programm `openssl` (wie schon vbmc.ensure_cert). Fehlt es, werden Zertifikate nur auf die
PEM-Umrahmung geprüft und CSRs nicht erzeugt."""
import calendar
import os
import re
import subprocess
import time

from .ossl import find_openssl


def _run(args, stdin=None):
    try:
        r = subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=20,
                           env={**os.environ, "MSYS2_ARG_CONV_EXCL": "*", "MSYS_NO_PATHCONV": "1"})
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def have_openssl():
    return find_openssl() is not None


def _date(s):
    try:
        return calendar.timegm(time.strptime(s.strip(), "%b %d %H:%M:%S %Y GMT"))
    except ValueError:
        return 0


def _cn(dn):
    m = re.search(r"CN=([^,]+)", dn or "")
    return m.group(1).strip() if m else (dn or "")


def _field(out, key):
    m = re.search(rf"^{re.escape(key)}=(.*)$", out, re.M | re.I)
    return m.group(1).strip() if m else ""


def parse_pem(pem):
    """Zertifikat-PEM lesen. Liefert ein Dict (subject, issuer, not_before, not_after, serial, fingerprint)
    oder None, wenn es kein gültiges Zertifikat ist."""
    pem = (pem or "").strip()
    if "-----BEGIN CERTIFICATE-----" not in pem or "-----END CERTIFICATE-----" not in pem:
        return None
    if not have_openssl():
        return {"subject": "unknown", "issuer": "unknown", "not_before": 0, "not_after": 0, "serial": "",
                "fingerprint": ""}
    out = _run([find_openssl(), "x509", "-noout", "-subject", "-issuer", "-startdate", "-enddate", "-serial",
                "-fingerprint", "-sha256", "-nameopt", "RFC2253"], pem)
    if not out:
        return None
    return {"subject": _cn(_field(out, "subject")), "issuer": _cn(_field(out, "issuer")),
            "subject_dn": _field(out, "subject"), "issuer_dn": _field(out, "issuer"),
            "not_before": _date(_field(out, "notBefore")), "not_after": _date(_field(out, "notAfter")),
            "serial": _field(out, "serial"), "fingerprint": _field(out, "sha256 Fingerprint")}


def _clean(s):
    return re.sub(r"[/\\=\r\n]", "", str(s or "")).strip()


def make_csr(common_name, org="", country="", state="", city=""):
    """CSR als PEM (der private Schlüssel wird verworfen) oder None."""
    cn = _clean(common_name)
    if not cn or not have_openssl():
        return None
    subj = "".join(f"/{k}={v}" for k, v in (("C", _clean(country)[:2]), ("ST", _clean(state)), ("L", _clean(city)),
                                             ("O", _clean(org)), ("CN", cn)) if v)
    out = _run([find_openssl(), "req", "-new", "-newkey", "rsa:2048", "-nodes", "-keyout", os.devnull, "-subj", subj])
    return out if out and "BEGIN CERTIFICATE REQUEST" in out else None
