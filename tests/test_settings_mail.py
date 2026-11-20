"""Einstellungsmanager (Schrift, Protokoll, Systemaudit, E-Mail, Experten) und Versand des Audits per E-Mail."""
import email
import pathlib
import socketserver
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from tests._support import qapp
from tests.test_vbmc import FACTORY_LOGIN, _WebBase


class _SMTP(socketserver.StreamRequestHandler):
    """Kleinster SMTP-Server für Tests (ohne TLS): merkt sich Absender, Empfänger und Nachricht."""
    box = []

    def say(self, line):
        self.wfile.write((line + "\r\n").encode())

    def handle(self):
        self.say("220 test")
        mail = {"rcpt": [], "auth": False}
        while True:
            line = self.rfile.readline().decode(errors="replace").strip()
            if not line:
                return
            cmd = line.split(" ", 1)[0].upper()
            if cmd in ("EHLO", "HELO"):
                self.wfile.write(b"250-test\r\n250 AUTH PLAIN LOGIN\r\n")
            elif cmd == "AUTH":
                mail["auth"] = True
                self.say("235 ok")
            elif cmd == "MAIL":
                mail["from"] = line
                self.say("250 ok")
            elif cmd == "RCPT":
                mail["rcpt"].append(line)
                self.say("250 ok")
            elif cmd == "DATA":
                self.say("354 go")
                data = []
                while True:
                    d = self.rfile.readline()
                    if d in (b".\r\n", b".\n", b""):
                        break
                    data.append(d)
                mail["data"] = b"".join(data)
                _SMTP.box.append(dict(mail))
                self.say("250 queued")
            elif cmd == "QUIT":
                self.say("221 bye")
                return
            else:
                self.say("250 ok")


def smtp_server(testcase):
    _SMTP.box = []
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _SMTP)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    testcase.addCleanup(srv.server_close)
    testcase.addCleanup(srv.shutdown)
    return srv.server_address[1]


class TestSettings(_WebBase):
    def setUp(self):
        qapp()
        self._isolate_data()

    def test_defaults_roundtrip_and_clamping(self):
        from bmc_steward import settings
        self.assertEqual(settings.get("verify_wait"), 240)
        self.assertTrue(settings.get("audit_auto_read"))
        settings.save({"font_size": 99, "verify_wait": 5, "smtp_host": " mail.example.org ", "unbekannt": 1})
        s = settings.load()
        self.assertEqual(s["font_size"], 20)                 # auf den erlaubten Bereich begrenzt
        self.assertEqual(s["verify_wait"], 30)
        self.assertEqual(s["smtp_host"], "mail.example.org")
        self.assertNotIn("unbekannt", s)

    def test_apply_font_log_and_timeout(self):
        from PyQt6.QtWidgets import QApplication
        from bmc_steward import applog, settings
        from bmc_steward.redfish import RedfishClient
        app = QApplication.instance()
        old_font, old_level, old_timeout = app.font(), applog.LOG.level, RedfishClient.TIMEOUT
        self.addCleanup(app.setFont, old_font)
        self.addCleanup(applog.LOG.set_level, old_level)
        self.addCleanup(setattr, RedfishClient, "TIMEOUT", old_timeout)
        self.addCleanup(applog.LOG.set_file, None)
        settings.save({"font_size": 13, "log_level": "debug", "http_timeout": 45, "log_file": True})
        settings.apply(app)
        self.assertEqual(app.font().pointSize(), 13)
        self.assertEqual(applog.LOG.level, applog.DEBUG)
        self.assertEqual(RedfishClient.TIMEOUT, 45)
        applog.LOG.info("Zeile für die Datei")
        files = list(settings.log_dir().glob("bmc-steward-*.log"))
        self.assertEqual(len(files), 1)
        self.assertIn("Zeile für die Datei", files[0].read_text(encoding="utf-8"))

    def test_old_log_files_are_removed(self):
        import os
        from bmc_steward import settings
        d = settings.log_dir()
        d.mkdir(parents=True, exist_ok=True)
        old, new = d / "bmc-steward-20200101.log", d / "bmc-steward-20990101.log"
        old.write_text("x")
        new.write_text("y")
        os.utime(old, (time.time() - 90 * 86400,) * 2)
        settings.cleanup_logs(30)
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())

    def test_dialog_saves(self):
        from bmc_steward import settings
        dlg = settings.SettingsDialog(None)
        self.addCleanup(dlg.close)
        dlg.widgets["font_size"].setValue(12)
        dlg.widgets["smtp_host"].setText("smtp.example.org")
        dlg.widgets["audit_auto_read"].setChecked(False)
        dlg.save()
        s = settings.load()
        self.assertEqual((s["font_size"], s["smtp_host"], s["audit_auto_read"]), (12, "smtp.example.org", False))

    def test_live_run_uses_verify_wait_setting(self):
        from bmc_steward import i18n, settings
        i18n.set_language("de")
        from bmc_steward.main import Window
        settings.save({"verify_wait": 90})
        w = Window()
        self.addCleanup(w.deleteLater)
        w.table.setRowCount(0)
        w.add_row(["10.0.0.5", "calvin", "10.0.1.5", "255.255.255.0", "10.0.1.1", "srv-a", ""])
        w.new_user.setText("klicker")
        w.new_pass.setText("Geheim123!")
        w.new_pass2.setText("Geheim123!")
        seen = {}

        class Fake:
            def __init__(self, parent, **kw):
                seen.update(kw)
                self.line = self.row_done = self.result = self

            def connect(self, *_):
                pass

            def start(self):
                pass

            def isRunning(self):
                return False
        with patch("bmc_steward.main.EngineThread", Fake):
            w.run()
        self.assertEqual(seen.get("verify_wait"), 90)


class TestMail(_WebBase):
    def setUp(self):
        qapp()
        self._isolate_data()

    def test_send_with_attachment(self):
        from bmc_steward import mailer
        port = smtp_server(self)
        pdf = pathlib.Path(tempfile.mkdtemp(prefix="bmc-steward-mail-")) / "Systemaudit-X.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")
        cfg = {"smtp_host": "127.0.0.1", "smtp_port": port, "smtp_security": "none", "smtp_user": "",
               "smtp_from": "audit@example.org"}
        mailer.send(cfg, "", ["erika@example.org", "chef@example.org"], "Audit X", "Hallo", [pdf])
        self.assertEqual(len(_SMTP.box), 1)
        msg = email.message_from_bytes(_SMTP.box[0]["data"])
        self.assertEqual(msg["Subject"], "Audit X")
        self.assertIn("erika@example.org", msg["To"])
        self.assertEqual(len(_SMTP.box[0]["rcpt"]), 2)
        att = [p for p in msg.walk() if p.get_filename()]
        self.assertEqual(att[0].get_filename(), "Systemaudit-X.pdf")
        self.assertEqual(att[0].get_payload(decode=True), b"%PDF-1.4 test")

    def test_addresses_are_checked(self):
        from bmc_steward import mailer
        self.assertEqual(mailer.addresses("a@b.de; c@d.org,  "), ["a@b.de", "c@d.org"])
        with self.assertRaises(ValueError):
            mailer.addresses("kein-mail")

    def test_mail_client_command_attaches_file(self):
        from bmc_steward import mailer
        cmd = mailer.client_command(["a@b.de"], "Betreff", "Text", "/tmp/x.pdf", linux=True)
        self.assertEqual(cmd[0], "xdg-email")
        self.assertIn("--attach", cmd)
        self.assertIn("/tmp/x.pdf", cmd)
        self.assertEqual(cmd[-1], "a@b.de")

    def test_template_placeholders(self):
        from bmc_steward import mailer
        text = mailer.fill("Audit {seriennummer} vom {datum} ({mitarbeiter}) {unbekannt}",
                           {"seriennummer": "CZJ1", "datum": "01.01.2026", "mitarbeiter": "Max"})
        self.assertEqual(text, "Audit CZJ1 vom 01.01.2026 (Max) {unbekannt}")


class TestAuditPageMailAndAutoRead(_WebBase):
    def test_pick_reads_right_away(self):
        qapp()
        self._isolate_data()
        from bmc_steward.sysaudit import AuditPage
        page = AuditPage(None)
        self.addCleanup(page.close)
        page.sources = lambda: [("a (Neuware)", "10.0.0.5", "root", "x")]
        page.refresh()
        calls = []
        page.read = lambda: calls.append(page.addr.text())
        page.addr_box.activated.emit(0)
        self.assertEqual(calls, ["10.0.0.5"])

    def test_pick_without_auto_read(self):
        qapp()
        self._isolate_data()
        from bmc_steward import settings
        from bmc_steward.sysaudit import AuditPage
        settings.save({"audit_auto_read": False})
        page = AuditPage(None)
        self.addCleanup(page.close)
        page.sources = lambda: [("a (Neuware)", "10.0.0.5", "root", "x")]
        page.refresh()
        calls = []
        page.read = lambda: calls.append(1)
        page.addr_box.activated.emit(0)
        self.assertEqual((calls, page.addr.text()), ([], "10.0.0.5"))

    def test_audit_is_mailed_to_staff_member(self):
        qapp()
        self._isolate_data()
        from bmc_steward import settings
        from bmc_steward.sysaudit import AuditPage, MailDialog, save_staff
        port = smtp_server(self)
        settings.save({"smtp_host": "127.0.0.1", "smtp_port": port, "smtp_security": "none",
                       "smtp_from": "audit@example.org"})
        save_staff([{"name": "Erika", "email": "erika@example.org"}])
        page = AuditPage(None)
        self.addCleanup(page.close)
        page.reload_staff()
        page.staff.setCurrentIndex(page.staff.findText("Erika"))
        page.serial.setText("CZJ025045P")
        page.add_row(["501533-001", "DIMM 2GB", "2", "A / B"])
        dlg = MailDialog(page, page.pdf_for_mail)
        self.addCleanup(dlg.close)
        self.assertEqual(dlg.to.currentText(), "erika@example.org")
        self.assertIn("CZJ025045P", dlg.subject.text())
        self.assertTrue(dlg.send(wait=True), dlg.status.text())
        msg = email.message_from_bytes(_SMTP.box[0]["data"])
        att = [p for p in msg.walk() if p.get_filename()]
        self.assertTrue(att[0].get_payload(decode=True).startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
