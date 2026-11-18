"""Read-only IMAP access to a mailbox that only receives Mercadona digital tickets.

Runs in HA's executor. The folder is opened with EXAMINE and messages are read
with BODY.PEEK, so nothing is marked as read, moved or deleted. Only PDF
attachments are returned; message bodies and headers are discarded.
"""
from datetime import timedelta
import email
from email import policy
import imaplib
import re
import socket
import ssl

from .catalog import CatalogError

GMAIL_HOST = "imap.gmail.com"
SENDER = "mercadona"
MAX_MESSAGE_BYTES = 6 * 1024 * 1024
MAX_PDF_BYTES = 2 * 1024 * 1024
MAX_MESSAGES = 400
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class MercadonaMailError(CatalogError):
    def __init__(self, message, stage, **details):
        super().__init__(message, "mercadona_mail_error")
        self.diagnostics = {"stage": stage, **details}


def valid_host(value):
    return isinstance(value, str) and 3 <= len(value) <= 253 and bool(re.fullmatch(r"(?=.*[a-z])[a-z0-9-]+(?:\.[a-z0-9-]+)+", value.lower()))


def valid_folder(value):
    return isinstance(value, str) and 1 <= len(value) <= 200 and not any(c in value for c in '"\\\r\n') and value.isprintable()


def imap_date(day):
    return f"{day.day:02d}-{MONTHS[day.month-1]}-{day.year}"


def _pdf_parts(raw):
    message = email.message_from_bytes(raw, policy=policy.default)
    found = []
    for part in message.walk():
        if part.is_multipart():
            continue
        kind = part.get_content_type().lower()
        name = (part.get_filename() or "").lower()
        if kind != "application/pdf" and not (kind == "application/octet-stream" and name.endswith(".pdf")):
            continue
        content = part.get_payload(decode=True) or b""
        if content.startswith(b"%PDF-") and 0 < len(content) <= MAX_PDF_BYTES:
            found.append(content)
    return found


class MercadonaMailbox:
    """One short IMAP session per call; credentials are passed in, never stored here."""

    def __init__(self, host, port, username, password, folder="INBOX"):
        self.host, self.port, self.username, self.password, self.folder = host, port, username, password, folder

    def _open(self):
        try:
            client = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=ssl.create_default_context(), timeout=25)
        except (OSError, ssl.SSLError, imaplib.IMAP4.error) as err:
            raise MercadonaMailError("No se ha podido conectar con el servidor de correo. Revisa el servidor y el puerto.", "connect", response_class="network_error") from err
        try:
            client.login(self.username, self.password)
        except imaplib.IMAP4.error as err:
            self._close(client)
            raise MercadonaMailError("El servidor de correo ha rechazado el acceso. Revisa el correo y la contraseña de aplicación.", "login", response_class="auth_failed") from None
        except (OSError, ssl.SSLError) as err:
            self._close(client)
            raise MercadonaMailError("Se ha perdido la conexión con el servidor de correo.", "login", response_class="network_error") from err
        try:
            status, data = client.select('"' + self.folder + '"', readonly=True)
        except (imaplib.IMAP4.error, OSError) as err:
            self._close(client)
            raise MercadonaMailError("No se ha podido abrir la carpeta del correo.", "folder", response_class="folder_error") from err
        if status != "OK":
            self._close(client)
            raise MercadonaMailError("No existe la carpeta indicada en el correo.", "folder", response_class="folder_missing")
        validity = client.untagged_responses.get("UIDVALIDITY", [b""])[-1]
        return client, (validity.decode("ascii", "ignore") if isinstance(validity, bytes) else str(validity or ""))

    @staticmethod
    def _close(client):
        for action in (client.logout,):
            try:
                action()
            except (imaplib.IMAP4.error, OSError, ssl.SSLError):
                pass

    def check(self):
        client, validity = self._open()
        self._close(client)
        return validity

    def search(self, since):
        """UIDs of messages from Mercadona since a date (newest last)."""
        client, validity = self._open()
        try:
            status, data = client.uid("SEARCH", None, "FROM", '"' + SENDER + '"', "SINCE", imap_date(since))
            if status != "OK" or not data:
                raise MercadonaMailError("El servidor de correo no ha completado la búsqueda.", "search")
            uids = [value for value in (data[0] or b"").decode("ascii", "ignore").split() if value.isdigit()]
            return validity, uids[-MAX_MESSAGES:]
        except (imaplib.IMAP4.error, OSError, socket.timeout) as err:
            raise MercadonaMailError("El servidor de correo no ha completado la búsqueda.", "search", response_class="network_error") from err
        finally:
            self._close(client)

    def fetch(self, validity, uids):
        """PDF attachments by UID. Missing or oversized messages are omitted."""
        client, current = self._open()
        try:
            if validity and current and validity != current:
                raise MercadonaMailError("La carpeta del correo ha cambiado. Vuelve a consultar el historial.", "uidvalidity")
            result = {}
            for uid in uids:
                if not str(uid).isdigit():
                    continue
                status, data = client.uid("FETCH", str(uid), "(RFC822.SIZE)")
                size = re.search(rb"RFC822\.SIZE (\d+)", b" ".join(item if isinstance(item, bytes) else b" ".join(item) for item in data or [] if item))
                if status != "OK" or not size or int(size[1]) > MAX_MESSAGE_BYTES:
                    continue
                status, data = client.uid("FETCH", str(uid), "(BODY.PEEK[])")
                raw = next((item[1] for item in data or [] if isinstance(item, tuple) and len(item) == 2), None)
                if status != "OK" or not isinstance(raw, bytes) or len(raw) > MAX_MESSAGE_BYTES:
                    continue
                result[str(uid)] = _pdf_parts(raw)
            return result
        except (imaplib.IMAP4.error, OSError, socket.timeout) as err:
            raise MercadonaMailError("Se ha interrumpido la lectura del correo.", "fetch", response_class="network_error") from err
        finally:
            self._close(client)


def history_start(now, days=365):
    return (now - timedelta(days=days)).date()
