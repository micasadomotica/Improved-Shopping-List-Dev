"""Mercadona account: online shop and/or digital-ticket mailbox, each optional.

Online shop: only the renewable token and customer id persist; the password is
used once. Digital tickets: the mailbox credentials persist privately in HA and
are never returned to the panel or written to logs. The mailbox is read-only.
"""
import asyncio
from copy import deepcopy
import math
import re
import time

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .catalog import CatalogError
from .const import DOMAIN
from .dia_account import DiaAccount
from .mercadona_mail import GMAIL_HOST, MercadonaMailbox, MercadonaMailError, history_start, valid_folder, valid_host
from .mercadona_online import MAX_PAGES, MercadonaOnlineAPI, MercadonaOnlineError, auth_fields, order_page, shape
from .receipt_parser import extract_receipt

STORE = "mercadona"
EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
BATCH = 20


class MercadonaAccountError(CatalogError):
    def __init__(self, message, stage, **details):
        super().__init__(message, "mercadona_account_error")
        self.diagnostics = {"stage": stage, **details}


def _parse_pdfs(pdfs):
    """Executor job: (index, receipt) for each Mercadona PDF; other PDFs are skipped."""
    result = []
    for index, content in enumerate(pdfs[:5]):
        try:
            receipt = extract_receipt(content)
        except CatalogError:
            continue
        if receipt.get("store") == STORE and receipt.get("identity"):
            result.append((index, receipt))
    return result


class MercadonaAccount(DiaAccount):
    """Reuses the account lifecycle and scheduling; both sources are Mercadona-specific."""

    def __init__(self, hass, receipts, activity):
        super().__init__(hass, receipts, activity)
        self._store = Store(hass, 1, f"{DOMAIN}.mercadona_account", private=True)
        self._credentials_store = Store(hass, 1, f"{DOMAIN}.mercadona_mail", private=True)
        self._options_store = Store(hass, 1, f"{DOMAIN}.mercadona_options", private=True)
        self._session = {"online": {}, "purchase_refs": {}, "mail_seen": {"validity": "", "uids": []}, "online_orders": None}
        self._mail = {}
        self._access = ""
        self._api = MercadonaOnlineAPI(hass)

    # ------------------------------------------------------------- storage

    async def async_load(self):
        data = await self._store.async_load()
        if isinstance(data, dict):
            online = data.get("online") if isinstance(data.get("online"), dict) else {}
            if all(isinstance(online.get(key), str) and 1 <= len(online[key]) <= 20000 for key in ("refresh_token", "customer_id")):
                self._session["online"] = {key: online[key] for key in ("refresh_token", "customer_id")}
            refs = data.get("purchase_refs")
            if isinstance(refs, dict) and len(refs) <= 1500:
                self._session["purchase_refs"] = {key: value for key, value in refs.items() if isinstance(key, str) and isinstance(value, dict)}
            seen = data.get("mail_seen")
            if isinstance(seen, dict) and isinstance(seen.get("validity"), str) and isinstance(seen.get("uids"), list):
                self._session["mail_seen"] = {"validity": seen["validity"][:64], "uids": [uid for uid in seen["uids"] if isinstance(uid, str) and uid.isdigit()][-2000:]}
            if type(data.get("online_orders")) is int and 0 <= data["online_orders"] <= 100000:
                self._session["online_orders"] = data["online_orders"]
        mail = await self._credentials_store.async_load()
        if isinstance(mail, dict) and self._valid_mail(mail):
            self._mail = {key: mail[key] for key in ("provider", "host", "port", "username", "password", "folder")}
        options = await self._options_store.async_load()
        if isinstance(options, dict):
            for key in ("history_enabled", "auto_ticket"):
                if type(options.get(key)) is bool:
                    self._options[key] = options[key]
            for key in ("completion_at", "handled_completion"):
                value = options.get(key)
                if type(value) in (int, float) and math.isfinite(value) and 0 <= value < 1e12:
                    self._options[key] = value
            if isinstance(options.get("ticket_day"), str):
                self._options["ticket_day"] = options["ticket_day"][:10]

    @staticmethod
    def _valid_mail(mail):
        return (mail.get("provider") in ("gmail", "other") and valid_host(mail.get("host")) and type(mail.get("port")) is int
            and 1 <= mail["port"] <= 65535 and isinstance(mail.get("username"), str) and 1 <= len(mail["username"]) <= 255
            and isinstance(mail.get("password"), str) and 1 <= len(mail["password"]) <= 255 and valid_folder(mail.get("folder")))

    async def _save(self):
        await self._store.async_save(self._session)

    # -------------------------------------------------------------- status

    def _online_connected(self):
        return bool(self._session["online"].get("refresh_token"))

    def _mail_connected(self):
        return bool(self._mail)

    def _connected(self):
        return self._online_connected() or self._mail_connected()

    def status(self, owner):
        return {"connected": self._connected(), "email": "",
            "options": {key: self._options[key] for key in ("history_enabled", "auto_ticket")},
            "online": {"connected": self._online_connected(), "orders": self._session["online_orders"] if self._online_connected() else None,
                "tickets_supported": False},
            "mail": {"connected": self._mail_connected(), "provider": self._mail.get("provider", ""), "username": self._mail.get("username", ""),
                "host": self._mail.get("host", ""), "port": self._mail.get("port"), "folder": self._mail.get("folder", ""),
                "tickets": sum(ref.get("source") == "mail" for ref in self._session["purchase_refs"].values()) if self._mail_connected() else 0}}

    async def async_options(self, owner, options):
        if not isinstance(options, dict) or set(options) != {"history_enabled", "auto_ticket"} or any(type(v) is not bool for v in options.values()):
            raise MercadonaAccountError("Las opciones de Mercadona no son válidas.", "options")
        if not self._connected() and any(options.values()):
            raise MercadonaAccountError("Conecta primero la tienda online o el ticket digital de Mercadona.", "not_connected")
        recover = options["history_enabled"] and not self._options["history_enabled"]
        self._options.update(options)
        await self._options_store.async_save(self._options)
        if recover and self._mail_connected():
            await self.async_sync_history(owner)
        self._queue_background()
        return self.status(owner)

    # --------------------------------------------------------- online shop

    def _check_active(self):
        if not self._active:
            raise MercadonaAccountError("La integración ya no está activa.", "inactive")

    async def _refresh(self, generation):
        data = await self._api.request("POST", "/auth/tokens/", body={"refresh_token": self._session["online"]["refresh_token"]})
        if not self._active or generation != self._generation:
            raise MercadonaAccountError("La conexión de Mercadona ha cambiado.", "inactive")
        access, refresh, customer = auth_fields(data)
        if not access:
            raise MercadonaOnlineError("Mercadona no ha renovado la sesión.", "token_format")
        self._access = access
        changed = False
        if refresh and refresh != self._session["online"]["refresh_token"]:
            self._session["online"]["refresh_token"] = refresh
            changed = True
        if customer and customer != self._session["online"]["customer_id"]:
            raise MercadonaOnlineError("Mercadona ha devuelto otra cuenta de cliente.", "token_customer")
        if changed:
            await self._save()

    async def async_login(self, owner, username, password, remember_credentials=False):
        """Online shop login. The password is never stored, whatever the flag says."""
        if (not isinstance(username, str) or not EMAIL.fullmatch(username.strip()) or len(username) > 255
                or not isinstance(password, str) or not 1 <= len(password) <= 255):
            raise MercadonaAccountError("Indica el correo y la contraseña de tu cuenta de la tienda online.", "login")
        self._check_active()
        async with self._lock:
            generation = self._generation
            data = await self._api.request("POST", "/auth/tokens/", body={"username": username.strip(), "password": password})
            password = ""
            access, refresh, customer = auth_fields(data)
            if not refresh or not customer:
                raise MercadonaOnlineError("Mercadona no ha confirmado una sesión renovable.", "token_format",
                    fields=sorted(str(key)[:40] for key in data)[:20] if isinstance(data, dict) else [])
            if not self._active or generation != self._generation:
                raise MercadonaAccountError("La conexión de Mercadona ha cambiado.", "inactive")
            previous, previous_access = deepcopy(self._session["online"]), self._access
            self._session["online"] = {"refresh_token": refresh, "customer_id": customer}
            self._access = access
            try:
                if not access:
                    await self._refresh(generation)
            except BaseException:
                self._session["online"], self._access = previous, previous_access
                raise
            if previous.get("customer_id") not in (None, customer):
                self._session["purchase_refs"] = {key: value for key, value in self._session["purchase_refs"].items() if value.get("source") != "online"}
            self._session["online_orders"] = None
            await self._save()
        try:
            await self.async_online_orders(owner)
        except ERRORS as err:
            self.activity.record("stores.account", "Mercadona: no se han podido consultar los pedidos online", source="manual",
                diagnostics={"store": STORE, **getattr(err, "diagnostics", {})})
        return self.status(owner)

    async def _online_get(self, path, query=None):
        if not self._online_connected():
            raise MercadonaAccountError("Conecta la tienda online de Mercadona en Ajustes → Tiendas.", "not_connected")
        self._check_active()
        generation = self._generation
        try:
            if not self._access:
                await self._refresh(generation)
            try:
                return await self._api.request("GET", path, access=self._access, query=query)
            except MercadonaOnlineError as err:
                if err.diagnostics.get("http_status") != 401:
                    raise
                await self._refresh(generation)
                return await self._api.request("GET", path, access=self._access, query=query)
        except MercadonaOnlineError as err:
            if err.diagnostics.get("http_status") in (400, 401) and err.diagnostics.get("stage") in ("/auth/tokens/", "/customers/{id}/orders/"):
                self._session["online"] = {}
                self._access = ""
                await self._save()
            raise
        finally:
            if not self._active or generation != self._generation:
                self._access = ""

    async def async_online_orders(self, owner):
        """Count online orders; their detail is imported once its format is verified."""
        async with self._lock:
            customer = self._session["online"].get("customer_id", "")
            count, page = 0, None
            for _ in range(MAX_PAGES):
                data = await self._online_get(f"/customers/{customer}/orders/", {"page": page} if page else None)
                rows, page = order_page(data)
                count += len(rows)
                if page is None:
                    break
            self._session["online_orders"] = count
            await self._save()
            return {"orders": count}

    async def async_online_diagnostic(self, owner):
        """Structure (field names and types, never values) of the order list and one order."""
        async with self._lock:
            customer = self._session["online"].get("customer_id", "")
            data = await self._online_get(f"/customers/{customer}/orders/")
            rows, _page = order_page(data)
            result = {"orders": shape(data), "order_count_first_page": len(rows), "detail": {}}
            first = rows[0] if rows and isinstance(rows[0], dict) else {}
            order = first.get("id", first.get("order_id"))
            if type(order) in (int, str) and re.fullmatch(r"[A-Za-z0-9-]{1,64}", str(order)):
                for name, path in (("order", f"/customers/{customer}/orders/{order}/"),
                                   ("prepared_lines", f"/customers/{customer}/orders/{order}/lines/prepared/"),
                                   ("lines", f"/customers/{customer}/orders/{order}/lines/")):
                    try:
                        result["detail"][name] = shape(await self._online_get(path))
                    except MercadonaOnlineError as err:
                        result["detail"][name] = {"http_status": err.diagnostics.get("http_status"), "response_class": err.diagnostics.get("response_class", "")}
            self.activity.record("stores.account", "Mercadona: estructura de pedidos online consultada", source="manual",
                diagnostics={"store": STORE, "orders": len(rows)})
            return result

    async def async_online_disconnect(self, owner):
        async with self._lock:
            self._generation += 1
            self._session["online"] = {}
            self._session["online_orders"] = None
            self._access = ""
            self._session["purchase_refs"] = {key: value for key, value in self._session["purchase_refs"].items() if value.get("source") != "online"}
            await self._save()
            await self._disable_options_if_disconnected()
        return self.status(owner)

    async def _disable_options_if_disconnected(self):
        if not self._connected():
            self._options.update(history_enabled=False, auto_ticket=False)
            await self._options_store.async_save(self._options)

    # ------------------------------------------------------ digital tickets

    def _mailbox(self, mail=None):
        mail = mail or self._mail
        return MercadonaMailbox(mail["host"], mail["port"], mail["username"], mail["password"], mail["folder"])

    async def async_mail_connect(self, owner, provider, username, password, host="", port=993, folder="INBOX"):
        if provider not in ("gmail", "other") or not isinstance(username, str) or not EMAIL.fullmatch(username.strip()) or len(username) > 255:
            raise MercadonaAccountError("Indica el correo que recibe los tickets digitales.", "mail_login")
        username = username.strip()
        if not isinstance(password, str) or len(password) > 255:
            raise MercadonaAccountError("Indica la contraseña de aplicación del correo.", "mail_login")
        password = "".join(password.split()) if provider == "gmail" else password
        if not password:
            if self._mail.get("username") != username or not self._mail.get("password"):
                raise MercadonaAccountError("Indica la contraseña de aplicación del correo.", "mail_login")
            password = self._mail["password"]
        if provider == "gmail":
            host, port, folder = GMAIL_HOST, 993, "INBOX"
        host = host.strip().lower() if isinstance(host, str) else ""
        folder = folder.strip() if isinstance(folder, str) else ""
        mail = {"provider": provider, "host": host, "port": port, "username": username, "password": password, "folder": folder or "INBOX"}
        if not self._valid_mail(mail):
            raise MercadonaAccountError("Revisa el servidor IMAP, el puerto y la carpeta.", "mail_login")
        self._check_active()
        async with self._lock:
            generation = self._generation
            validity = await self.hass.async_add_executor_job(self._mailbox(mail).check)
            if not self._active or generation != self._generation:
                raise MercadonaAccountError("La conexión de Mercadona ha cambiado.", "inactive")
            if any(self._mail.get(key) != mail[key] for key in ("host", "username", "folder")):
                self._session["purchase_refs"] = {key: value for key, value in self._session["purchase_refs"].items() if value.get("source") != "mail"}
                self._session["mail_seen"] = {"validity": validity, "uids": []}
            await self._credentials_store.async_save(mail)
            self._mail = mail
            await self._save()
        if self._options["history_enabled"]:
            await self.async_sync_history(owner)
        return self.status(owner)

    async def async_mail_disconnect(self, owner):
        async with self._lock:
            self._generation += 1
            await self._credentials_store.async_save({})
            self._mail = {}
            self._session["purchase_refs"] = {key: value for key, value in self._session["purchase_refs"].items() if value.get("source") != "mail"}
            self._session["mail_seen"] = {"validity": "", "uids": []}
            await self._save()
            await self._disable_options_if_disconnected()
        return self.status(owner)

    async def async_disconnect(self, owner):
        await self.async_online_disconnect(owner)
        return await self.async_mail_disconnect(owner)

    async def _scan_mail(self):
        """Parse the PDFs of mailbox messages not seen before; returns refs of valid tickets."""
        generation = self._generation
        now = dt_util.now()
        validity, uids = await self.hass.async_add_executor_job(self._mailbox().search, history_start(now))
        seen = self._session["mail_seen"]
        if seen["validity"] != validity:
            seen.update(validity=validity, uids=[])
            self._session["purchase_refs"] = {key: value for key, value in self._session["purchase_refs"].items() if value.get("source") != "mail"}
        known = set(seen["uids"])
        pending = [uid for uid in uids if uid not in known]
        for start in range(0, len(pending), BATCH):
            chunk = pending[start:start+BATCH]
            messages = await self.hass.async_add_executor_job(self._mailbox().fetch, validity, chunk)
            for uid in chunk:
                for index, receipt in await self.hass.async_add_executor_job(_parse_pdfs, messages.get(uid, [])):
                    reference = "ticket:" + receipt["identity"]
                    key = self.receipts._account_key(reference, STORE)
                    self._session["purchase_refs"][key] = {"source": "mail", "reference": reference, "amount": receipt["total"],
                        "purchased_at": receipt["purchased_at"], "uid": uid, "validity": validity, "index": index,
                        "article_count": receipt.get("article_count")}
            if not self._active or generation != self._generation:
                raise MercadonaAccountError("La conexión de Mercadona ha cambiado.", "inactive")
            seen["uids"] = (seen["uids"] + chunk)[-2000:]
            await self._save()
            if start + BATCH < len(pending):
                await asyncio.sleep(1)
        limit = history_start(now)
        refs = self._session["purchase_refs"]
        self._session["purchase_refs"] = dict(sorted(refs.items(), key=lambda item: item[1].get("purchased_at", ""))[-1000:])
        return [(key, ref) for key, ref in self._session["purchase_refs"].items()
                if ref.get("source") == "mail" and (date := dt_util.parse_datetime(ref.get("purchased_at", ""))) and date.date() >= limit]

    async def async_purchases(self, owner, cursor=None, *, import_summaries=None):
        if cursor is not None:
            raise MercadonaAccountError("La página de compras de Mercadona no es válida.", "pagination")
        if not self._connected():
            raise MercadonaAccountError("Conecta la tienda online o el ticket digital de Mercadona en Ajustes → Tiendas.", "not_connected")
        async with self._lock:
            output, summaries = [], []
            if self._mail_connected():
                for key, ref in await self._scan_mail():
                    summaries.append({"purchaseId": ref["reference"], "purchaseDate": ref["purchased_at"], "amount": ref["amount"],
                        "article_count": ref.get("article_count"), "ticket_available": True})
                    output.append({"id": key, "purchased_at": ref["purchased_at"], "amount": ref["amount"], "store": "Mercadona",
                        "reviewable": True, "kind": "Ticket digital"})
            enabled = self._options["history_enabled"] if import_summaries is None else import_summaries
            added = await self.receipts.async_import_account_purchases(summaries, STORE) if enabled and summaries else 0
            output.sort(key=lambda row: row["purchased_at"], reverse=True)
            return {"purchases": output, "next_cursor": None, "added": added}

    async def async_sync_history(self, owner):
        if self._sync_lock.locked():
            raise MercadonaAccountError("Ya se está incorporando el historial de Mercadona.", "history_sync")
        async with self._sync_lock:
            result = await self.async_purchases(owner, import_summaries=True)
            return {"added": result["added"], "available": len(result["purchases"]), "complete": True}

    async def async_preview_saved(self, owner, receipt_id):
        saved = self.receipts.stock.receipts["history"].get(receipt_id)
        if not saved or saved.get("store") != STORE or not saved.get("summary_only") or not saved.get("account_key"):
            raise MercadonaAccountError("Esta compra no tiene un ticket pendiente de Mercadona.", "saved_purchase")
        key = saved["account_key"]
        if key not in self._session["purchase_refs"]:
            await self.async_sync_history(owner)
        return await self.async_preview(owner, key)

    async def async_preview(self, owner, purchase_id):
        async with self._lock:
            ref = self._session["purchase_refs"].get(purchase_id)
            if not isinstance(ref, dict) or self.receipts._account_key(ref.get("reference", ""), STORE) != purchase_id:
                raise MercadonaAccountError("Vuelve a consultar las compras de Mercadona antes de abrir este ticket.", "purchase_unavailable")
            if ref.get("source") != "mail":
                raise MercadonaAccountError("El detalle de los pedidos online todavía no está disponible.", "online_detail")
            if not self._mail_connected():
                raise MercadonaAccountError("Conecta el ticket digital de Mercadona en Ajustes → Tiendas.", "not_connected")
            messages = await self.hass.async_add_executor_job(self._mailbox().fetch, ref["validity"], [ref["uid"]])
            pdfs = messages.get(ref["uid"], [])
            if not 0 <= ref.get("index", 0) < len(pdfs):
                raise MercadonaAccountError("El correo del ticket ya no está disponible en el buzón.", "mail_missing")
            content = pdfs[ref.get("index", 0)]
        return await self.receipts.async_preview_mercadona_pdf(content, ref["reference"], ref["amount"], owner)

    # ----------------------------------------------------------- automatic

    def note_completion(self):
        if not self._active or not self._options["auto_ticket"] or not self._mail_connected():
            return
        self._options["completion_at"] = time.time()
        self._queue_background()

    def _queue_background(self, *_args):
        if self._active and self._options["auto_ticket"] and (self._task is None or self._task.done()):
            self._task = self.hass.async_create_background_task(self._run_background(), "Mercadona automatic tickets")

    def automatic_due(self, now, detected_today):
        day = now.date().isoformat()
        return bool(self._active and self._mail_connected() and self._options["auto_ticket"] and self._options["ticket_day"] != day
            and self._options["completion_at"] > self._options["handled_completion"]
            and (detected_today or now.strftime("%H:%M") >= self.settings.snapshot["price_update_time"]))

    async def async_automatic_ticket(self, now, detected_today):
        if not self.automatic_due(now, detected_today):
            return False
        self._options["ticket_day"] = now.date().isoformat()
        await self._options_store.async_save(self._options)
        try:
            result = await self.async_purchases("automatic_mercadona")
            candidates = result["purchases"]
            if detected_today:
                candidates = [p for p in candidates if dt_util.parse_datetime(p["purchased_at"]).date() == now.date()]
            latest = max(candidates, key=lambda p: p["purchased_at"], default=None)
            if latest and self._active and self._options["auto_ticket"]:
                reference = self._session["purchase_refs"][latest["id"]]["reference"]
                if not self.receipts.account_ticket_available(reference, STORE):
                    draft = await self.async_preview("automatic_mercadona", latest["id"])
                    if self._active and self._options["auto_ticket"]:
                        await self.receipts.async_archive_account_ticket(draft["token"], "automatic_mercadona")
                    else:
                        self.receipts.discard(draft["token"], "automatic_mercadona")
                self.activity.record("stores.account", "Mercadona: último ticket digital disponible para revisar", source="automatic")
            self._options["handled_completion"] = self._options["completion_at"]
            return bool(latest and dt_util.parse_datetime(latest["purchased_at"]).date() == now.date())
        finally:
            await self._options_store.async_save(self._options)


ERRORS = (MercadonaAccountError, MercadonaOnlineError, MercadonaMailError)
