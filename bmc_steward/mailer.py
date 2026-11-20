"""BMC-Steward: Audit-PDFs per E-Mail verschicken.

Zwei Wege: direkt über SMTP (Server aus den Einstellungen, STARTTLS oder SSL mit Zertifikatsprüfung) oder
über das eigene Mailprogramm (Linux: xdg-email mit Anhang, z. B. Thunderbird). Das SMTP-Passwort steht nie im
Protokoll und nie in einer Datei; auf Wunsch liegt es im System-Schlüsselbund.
"""
import mimetypes
import re
import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

ADDR = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


def addresses(text):
    """„a@b.de; c@d.org“ → Liste; ValueError bei einer ungültigen Adresse."""
    out = [a.strip() for a in re.split(r"[,;\s]+", text or "") if a.strip()]
    bad = [a for a in out if not ADDR.match(a)]
    if bad:
        raise ValueError(", ".join(bad))
    return out


def fill(template, values):
    """Platzhalter wie {seriennummer} ersetzen; unbekannte bleiben stehen."""
    return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), m.group(0))), template or "")


def build(sender, to, cc, subject, body, attachments):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=(sender.rsplit("@", 1)[-1] if "@" in sender else None))
    msg.set_content(body)
    for path in attachments:
        path = Path(path)
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        main, sub = ctype.split("/", 1)
        msg.add_attachment(path.read_bytes(), maintype=main, subtype=sub, filename=path.name)
    return msg


def send(cfg, password, to, subject, body, attachments, cc=(), timeout=30):
    """Verschickt über SMTP. `cfg`: smtp_host, smtp_port, smtp_security (starttls|ssl|none), smtp_user, smtp_from.
    Wirft OSError/smtplib.SMTPException mit verständlicher Ursache."""
    host, port, sec = cfg["smtp_host"], int(cfg["smtp_port"]), cfg["smtp_security"]
    if not host:
        raise ValueError("kein SMTP-Server eingestellt")
    sender = cfg.get("smtp_from") or cfg.get("smtp_user") or ""
    if not ADDR.match(sender):
        raise ValueError("kein gültiger Absender eingestellt")
    msg = build(sender, list(to), list(cc), subject, body, attachments)
    ctx = ssl.create_default_context()                  # prüft Zertifikat und Hostnamen des Mailservers
    if sec == "ssl":
        conn = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ctx)
    else:
        conn = smtplib.SMTP(host, port, timeout=timeout)
    try:
        conn.ehlo()
        if sec == "starttls":
            conn.starttls(context=ctx)
            conn.ehlo()
        if cfg.get("smtp_user"):
            conn.login(cfg["smtp_user"], password)
        conn.send_message(msg, from_addr=sender, to_addrs=list(to) + list(cc))
    finally:
        try:
            conn.quit()
        except (smtplib.SMTPException, OSError):
            conn.close()


def client_command(to, subject, body, attachment, linux=None):
    """Befehl, der das eigene Mailprogramm mit fertiger Nachricht und Anhang öffnet, oder None (kein Weg)."""
    linux = sys.platform.startswith("linux") if linux is None else linux
    if not linux:
        return None
    return ["xdg-email", "--utf8", "--subject", subject, "--body", body, "--attach", str(attachment)] + list(to)
