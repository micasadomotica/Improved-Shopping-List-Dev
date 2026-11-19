"""Mercadona account: optional online shop and read-only digital-ticket mailbox."""
from datetime import timedelta
from email.message import EmailMessage
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.improved_shopping_list_dev import mercadona_account as module
from custom_components.improved_shopping_list_dev import mercadona_mail
from custom_components.improved_shopping_list_dev.catalog import CatalogError
from custom_components.improved_shopping_list_dev.const import DOMAIN, CONF_TODO_ENTITY
from custom_components.improved_shopping_list_dev.mercadona_account import MercadonaAccount, MercadonaAccountError
from custom_components.improved_shopping_list_dev.mercadona_mail import MercadonaMailbox, _pdf_parts, imap_date, valid_folder, valid_host
from custom_components.improved_shopping_list_dev.mercadona_online import MercadonaOnlineError, auth_fields, order_page, shape
from custom_components.improved_shopping_list_dev.receipts import ReceiptManager


def receipt(identity="9999-999-100001", total=9.43, hours=2):
    when = (dt_util.now() - timedelta(hours=hours)).replace(tzinfo=None, second=0, microsecond=0)
    return {"store": "mercadona", "store_name": "Mercadona", "identity": identity, "purchased_at": when.isoformat(),
        "total": total, "article_count": 6, "lines": []}


@pytest.fixture
async def account(hass):
    receipts = SimpleNamespace(_account_key=ReceiptManager._account_key, async_import_account_purchases=AsyncMock(return_value=1),
        async_preview_mercadona_pdf=AsyncMock(return_value={"token": "draft"}), account_ticket_available=Mock(return_value=False),
        async_archive_account_ticket=AsyncMock(), discard=Mock(), stock=SimpleNamespace(receipts={"history": {}}))
    manager = MercadonaAccount(hass, receipts, SimpleNamespace(record=Mock()))
    await manager.async_load()
    manager.settings = SimpleNamespace(snapshot={"price_update_time": "00:00"})
    manager.start()
    yield manager
    await manager.async_stop()


class FakeMailbox:
    def __init__(self, messages):
        self.messages, self.fetched = messages, []

    def check(self):
        return "7"

    def search(self, since):
        assert since <= dt_util.now().date()
        return "7", list(self.messages)

    def fetch(self, validity, uids):
        assert validity == "7"
        self.fetched.append(list(uids))
        return {uid: self.messages[uid] for uid in uids if uid in self.messages}


def test_mail_helpers_only_return_pdf_attachments():
    message = EmailMessage()
    message["From"] = "Mercadona <ticket@example.test>"
    message.set_content("PRIVATE_BODY")
    message.add_attachment(b"%PDF-1.4 ticket", maintype="application", subtype="pdf", filename="ticket.pdf")
    message.add_attachment(b"%PDF-1.4 other", maintype="application", subtype="octet-stream", filename="copia.PDF")
    message.add_attachment(b"not a pdf", maintype="application", subtype="pdf", filename="fake.pdf")
    message.add_attachment(b"GIF89a", maintype="image", subtype="gif", filename="logo.gif")
    assert _pdf_parts(message.as_bytes()) == [b"%PDF-1.4 ticket", b"%PDF-1.4 other"]
    assert imap_date(dt_util.parse_datetime("2026-03-05T10:00:00").date()) == "05-Mar-2026"
    assert valid_host("imap.gmail.com") and not valid_host("imap gmail") and not valid_host("localhost")
    assert valid_folder("INBOX") and valid_folder("[Gmail]/Todos") and not valid_folder('IN"BOX') and not valid_folder("")


def test_mailbox_is_read_only(monkeypatch):
    calls = []

    class Client:
        untagged_responses = {"UIDVALIDITY": [b"7"]}

        def __init__(self, host, port, ssl_context=None, timeout=None):
            calls.append(("connect", host, port, timeout))

        def login(self, user, password):
            calls.append(("login", user))

        def select(self, folder, readonly=False):
            calls.append(("select", folder, readonly))
            return "OK", [b"1"]

        def uid(self, command, *args):
            calls.append(("uid", command) + tuple(args))
            if command == "SEARCH":
                return "OK", [b"3 4"]
            if args[1] == "(RFC822.SIZE)":
                return "OK", [b"3 (UID 3 RFC822.SIZE 100)"]
            return "OK", [(b"3 (UID 3 BODY[] {10}", b"Subject: x\r\n\r\nbody"), b")"]

        def logout(self):
            calls.append(("logout",))

    monkeypatch.setattr(mercadona_mail.imaplib, "IMAP4_SSL", Client)
    box = MercadonaMailbox("imap.example.test", 993, "tickets@example.test", "SECRET", "INBOX")
    assert box.search(dt_util.now().date())[1] == ["3", "4"]
    assert box.fetch("7", ["3"]) == {"3": []}
    assert ("select", '"INBOX"', True) in calls
    assert any(call[:4] == ("uid", "SEARCH", None, "FROM") and call[4] == '"mercadona"' for call in calls)
    assert all("STORE" not in call and "EXPUNGE" not in call for call in calls)
    assert ("uid", "FETCH", "3", "(BODY.PEEK[])") in calls and not any("(BODY[])" in call for call in calls)
    with pytest.raises(CatalogError):
        box.fetch("8", ["3"])


async def test_mail_connect_scans_once_imports_summaries_and_never_returns_password(account, hass, monkeypatch):
    first, second = receipt(), receipt("9999-999-100002", 4.5, 30)
    mailbox = FakeMailbox({"10": [b"%PDF-a"], "11": [b"%PDF-b"], "12": [b"%PDF-c"]})
    parsed = {b"%PDF-a": [(0, first)], b"%PDF-b": [(0, second)], b"%PDF-c": []}
    monkeypatch.setattr(module, "_parse_pdfs", lambda pdfs: [item for pdf in pdfs for item in parsed[pdf]])
    account._mailbox = lambda mail=None: mailbox
    status = await account.async_mail_connect("owner", "gmail", " tickets@example.test ", "abcd efgh ijkl mnop")
    assert status["mail"]["connected"] and status["mail"]["host"] == "imap.gmail.com" and status["connected"]
    assert "abcdefghijklmnop" not in json.dumps(status) and not status["online"]["connected"]
    assert (await account._credentials_store.async_load())["password"] == "abcdefghijklmnop"
    result = await account.async_purchases("owner", import_summaries=True)
    assert [row["amount"] for row in result["purchases"]] == [9.43, 4.5] and result["added"] == 1
    imported = account.receipts.async_import_account_purchases.await_args.args
    assert imported[1] == "mercadona" and {row["purchaseId"] for row in imported[0]} == {"ticket:9999-999-100001", "ticket:9999-999-100002"}
    assert result["purchases"][0]["id"] == ReceiptManager._account_key("ticket:9999-999-100001", "mercadona")
    assert account.status("owner")["mail"]["tickets"] == 2
    await account.async_purchases("owner")
    assert mailbox.fetched == [["10", "11", "12"]]
    await account.async_preview("owner", result["purchases"][0]["id"])
    account.receipts.async_preview_mercadona_pdf.assert_awaited_once_with(b"%PDF-a", "ticket:9999-999-100001", 9.43, "owner")
    restored = MercadonaAccount(hass, account.receipts, account.activity)
    await restored.async_load()
    assert restored.status("owner")["mail"]["tickets"] == 2 and restored._session["mail_seen"]["uids"] == ["10", "11", "12"]
    await account.async_mail_disconnect("owner")
    assert not account.status("owner")["connected"] and await account._credentials_store.async_load() == {}
    assert account.status("owner")["options"] == {"history_enabled": False, "auto_ticket": False}


async def test_mail_connect_validates_provider_fields(account):
    account._mailbox = lambda mail=None: FakeMailbox({})
    for args in (("gmail", "no-email", "x"), ("other", "a@example.test", "x", "bad host", 993, "INBOX"),
                 ("other", "a@example.test", "x", "imap.example.test", 0, "INBOX"), ("gmail", "a@example.test", "")):
        with pytest.raises(MercadonaAccountError):
            await account.async_mail_connect("owner", *args)
    status = await account.async_mail_connect("owner", "other", "a@example.test", "pass word", "IMAP.Example.test", 1993, "Tickets")
    assert status["mail"]["host"] == "imap.example.test" and status["mail"]["port"] == 1993 and status["mail"]["folder"] == "Tickets"
    assert (await account._credentials_store.async_load())["password"] == "pass word"
    await account.async_mail_connect("owner", "other", "a@example.test", "", "imap.example.test", 1993, "Tickets")
    assert (await account._credentials_store.async_load())["password"] == "pass word"


def test_online_helpers_keep_only_tokens_and_structure():
    assert auth_fields({"access_token": "A", "refresh_token": "R", "customer_id": "c-1"}) == ("A", "R", "c-1")
    assert auth_fields({"refresh": "R", "customer": {"id": 12}}) == ("", "R", "12")
    assert auth_fields({"refresh_token": "has space", "customer_id": "../x"}) == ("", "", "")
    described = shape({"results": [{"id": 1, "price": "12.30", "address": {"street": "PRIVATE"}}], "next_page": None})
    assert "PRIVATE" not in json.dumps(described) and "12.30" not in json.dumps(described)
    assert described["results"]["item"]["address"] == {"street": "str"} and described["next_page"] == "null"
    assert order_page({"results": [], "next_page": None}) == ([], None)
    assert order_page({"results": [{}], "next_page": "https://x/?page=2"}) == ([{}], 2)
    with pytest.raises(MercadonaOnlineError):
        order_page({"orders": []})


async def test_online_login_stores_refresh_token_only_and_reports_no_orders(account, hass):
    calls = []

    async def request(method, path, *, body=None, access="", query=None):
        calls.append((method, path, body, access))
        if path == "/auth/tokens/" and body.get("password"):
            return {"refresh_token": "SECRET_REFRESH", "customer_id": "cust-1"}
        if path == "/auth/tokens/":
            return {"access_token": "SECRET_ACCESS"}
        assert path == "/customers/cust-1/orders/" and access == "SECRET_ACCESS"
        return {"results": [], "next_page": None}

    account._api.request = request
    status = await account.async_login("owner", "client@example.test", "SECRET_PASSWORD")
    assert status["online"] == {"connected": True, "orders": 0, "tickets_supported": False} and not status["mail"]["connected"]
    stored = await account._store.async_load()
    assert stored["online"] == {"refresh_token": "SECRET_REFRESH", "customer_id": "cust-1"}
    assert "SECRET_PASSWORD" not in json.dumps(stored) and "SECRET_ACCESS" not in json.dumps(stored)
    assert "SECRET" not in json.dumps(status) and await account._credentials_store.async_load() is None
    assert [call[0] for call in calls] == ["POST", "POST", "GET"] and all(method in ("GET", "POST") for method, *_ in calls)


async def test_online_expired_session_refreshes_once_then_disconnects(account):
    account._session["online"] = {"refresh_token": "R", "customer_id": "cust-1"}
    account._access = "OLD"
    attempts = []

    async def request(method, path, *, body=None, access="", query=None):
        attempts.append(path)
        if path == "/auth/tokens/":
            raise MercadonaOnlineError("caducada", "/auth/tokens/", http_status=401)
        raise MercadonaOnlineError("caducada", "/customers/{id}/orders/", http_status=401)

    account._api.request = request
    with pytest.raises(MercadonaOnlineError):
        await account.async_online_orders("owner")
    assert attempts == ["/customers/cust-1/orders/", "/auth/tokens/"] and not account.status("owner")["online"]["connected"]


async def test_online_diagnostic_returns_structure_without_values(account):
    account._session["online"] = {"refresh_token": "R", "customer_id": "cust-1"}
    account._access = "A"

    async def request(method, path, *, body=None, access="", query=None):
        assert method == "GET"
        if path.endswith("/orders/"):
            return {"results": [{"id": 55, "summary": {"total": "PRIVATE_TOTAL"}}], "next_page": None}
        if path.endswith("/orders/55/"):
            return {"id": 55, "address": "PRIVATE_ADDRESS"}
        raise MercadonaOnlineError("no", "x", http_status=404, response_class="not_found")

    account._api.request = request
    result = await account.async_online_diagnostic("owner")
    assert "PRIVATE" not in json.dumps(result) and result["order_count_first_page"] == 1
    assert result["detail"]["order"] == {"id": "int", "address": "str"} and result["detail"]["lines"]["http_status"] == 404


async def test_online_orders_are_not_reviewable_yet_and_options_need_a_source(account):
    with pytest.raises(MercadonaAccountError):
        await account.async_options("owner", {"history_enabled": True, "auto_ticket": False})
    with pytest.raises(MercadonaAccountError):
        await account.async_purchases("owner")
    key = ReceiptManager._account_key("online:1", "mercadona")
    account._session["purchase_refs"][key] = {"source": "online", "reference": "online:1"}
    with pytest.raises(MercadonaAccountError):
        await account.async_preview("owner", key)


async def test_automatic_ticket_downloads_latest_mail_ticket_once_per_day(account, monkeypatch):
    mailbox = FakeMailbox({"10": [b"%PDF-a"]})
    monkeypatch.setattr(module, "_parse_pdfs", lambda pdfs: [(0, receipt(hours=0))] if pdfs else [])
    account._mailbox = lambda mail=None: mailbox
    await account.async_mail_connect("owner", "gmail", "tickets@example.test", "secret")
    await account.async_options("owner", {"history_enabled": False, "auto_ticket": True})
    account.note_completion()
    if account._task:
        await account._task
    now = dt_util.now()
    account._options["ticket_day"] = ""
    account._options["completion_at"] = account._options["handled_completion"] + 1
    assert account.automatic_due(now, True)
    assert await account.async_automatic_ticket(now, True)
    account.receipts.async_archive_account_ticket.assert_awaited_with("draft", "automatic_mercadona")
    assert not account.automatic_due(now, True)


async def test_history_import_keeps_ticket_lines_and_reads_only_tickets_without_them(account, monkeypatch):
    first, second, third = receipt(), receipt("9999-999-100002", 4.5, 30), receipt("9999-999-100003", 2.0, 40)
    for item in (first, second, third):
        item["lines"] = [{"id": "0", "description": "PAN", "count": 1, "unit_price": item["total"], "amount": item["total"]}]
    mailbox = FakeMailbox({"10": [b"%PDF-a"], "11": [b"%PDF-b"], "12": [b"%PDF-c"]})
    parsed = {b"%PDF-a": [(0, first)], b"%PDF-b": [(0, second)], b"%PDF-c": [(0, third)]}
    monkeypatch.setattr(module, "_parse_pdfs", lambda pdfs: [item for pdf in pdfs for item in parsed[pdf]])
    account._mailbox = lambda mail=None: mailbox
    await account.async_mail_connect("owner", "gmail", "tickets@example.test", "secret")
    await account.async_purchases("owner", import_summaries=False)
    assert mailbox.fetched == [["10", "11", "12"]] and account._parsed == {}
    key = lambda item: ReceiptManager._account_key("ticket:" + item["identity"], "mercadona")
    account.receipts.stock.receipts["history"] = {
        "a": {"account_key": key(first), "summary_only": True},
        "b": {"account_key": key(second), "summary_only": True, "pending_review": True}}
    await account.async_purchases("owner", import_summaries=True)
    # Only the earlier-seen summary without lines and the unsaved ticket are read again.
    assert [sorted(chunk) for chunk in mailbox.fetched[1:]] == [["10", "12"]]
    summaries = {row["purchaseId"]: row for row in account.receipts.async_import_account_purchases.await_args.args[0]}
    assert summaries["ticket:9999-999-100001"]["detail"] is first and summaries["ticket:9999-999-100003"]["detail"] is third
    assert "detail" not in summaries["ticket:9999-999-100002"] and account._parsed == {}


async def test_receipt_manager_stores_mercadona_history_lines_pending_review(hass):
    import asyncio
    from custom_components.improved_shopping_list_dev.catalog import Catalog
    from custom_components.improved_shopping_list_dev.stock import StockManager
    catalog = Catalog(hass); await catalog.async_load(); product = await catalog.async_save_product({"name": "Pan"})
    stock = StockManager(hass, catalog); await stock.async_load(); item = await stock.async_add("Pan")
    automation = SimpleNamespace(_lock=asyncio.Lock(), async_check=AsyncMock(), _snapshots={}, settings=SimpleNamespace(snapshot={"primary_entity": "todo.main"}))
    manager = ReceiptManager(hass, stock, automation, SimpleNamespace(_notify=Mock()))
    parsed = receipt(total=1.5)
    parsed["lines"] = [{"id": "0", "description": "PAN", "count": 1, "unit_price": 1.0, "amount": 1.0},
        {"id": "1", "description": "BOLSA", "count": 1, "unit_price": .5, "amount": .5}]
    wrong = receipt("9999-999-100002", 3.0)
    wrong["lines"] = list(parsed["lines"])
    rows = [{"purchaseId": "ticket:" + parsed["identity"], "purchaseDate": parsed["purchased_at"], "amount": 1.5, "detail": parsed},
        {"purchaseId": "ticket:" + wrong["identity"], "purchaseDate": wrong["purchased_at"], "amount": 4.0, "detail": wrong}]
    assert await manager.async_import_account_purchases(rows, "mercadona") == 2
    saved = {row["account_key"]: row for row in stock.receipts["history"].values()}
    pending = saved[ReceiptManager._account_key(rows[0]["purchaseId"], "mercadona")]
    assert pending["summary_only"] and pending["pending_review"] and len(pending["lines"]) == 2
    other = saved[ReceiptManager._account_key(rows[1]["purchaseId"], "mercadona")]
    assert other["summary_only"] and not other.get("pending_review") and other["lines"] == []
    assert stock.snapshot["items"][0]["quantity"] == item["quantity"] and not manager.offers_for(product)
    # A second import never overwrites a ticket that is already pending review.
    assert await manager.async_import_account_purchases(rows[:1], "mercadona") == 0
    review = await manager.async_review_saved(pending["id"], "owner")
    assert [line["description"] for line in review["receipt"]["lines"]] == ["PAN", "BOLSA"]
    choices = [{"id": line["id"], "product_id": product["id"] if i == 0 else "", "replenish": i == 0, "quantity": 1 if i == 0 else None,
        "unit": "uds", "purchase_id": "", "save_price": i == 0} for i, line in enumerate(review["receipt"]["lines"])]
    manager.settings = SimpleNamespace(snapshot={"purchase_detection": {"persons": ["person.ana", "person.luis"]}})
    with pytest.raises(CatalogError):
        await manager.async_confirm(review["token"], "owner", choices, True, shoppers=["person.other"])
    await manager.async_confirm(review["token"], "owner", choices, True, shoppers=["person.ana", "person.ana"])
    assert stock.snapshot["items"][0]["quantity"] == item["quantity"] + 1
    history = list(stock.receipts["history"].values())
    assert len(history) == 2 and sum(not row.get("summary_only") for row in history) == 1
    confirmed = next(row for row in history if not row.get("summary_only"))
    assert confirmed["shoppers"] == ["person.ana"]
    # Buyers can be changed later from the reviewed ticket; an empty choice removes them.
    reprocess = await manager.async_review_saved(confirmed["id"], "owner")
    lines = [{"id": line["id"], "product_id": line["product_id"], "pack_quantity": None, "pack_unit": ""} for line in reprocess["receipt"]["lines"]]
    await manager.async_reprocess(reprocess["token"], "owner", lines, shoppers=[])
    assert "shoppers" not in stock.receipts["history"][confirmed["id"]]


async def test_receipt_manager_checks_mercadona_mail_ticket_identity(hass):
    manager = ReceiptManager(hass, SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
    manager._prepare_preview = AsyncMock(side_effect=lambda receipt, owner: {"receipt": receipt})
    parsed = receipt()
    manager.hass = SimpleNamespace(async_add_executor_job=AsyncMock(return_value=dict(parsed)))
    result = await manager.async_preview_mercadona_pdf(b"%PDF", "ticket:9999-999-100001", 9.43, "owner")
    assert result["receipt"]["account_key"] == ReceiptManager._account_key("ticket:9999-999-100001", "mercadona")
    for reference, total in (("ticket:other", 9.43), ("ticket:9999-999-100001", 5.0)):
        manager.hass = SimpleNamespace(async_add_executor_job=AsyncMock(return_value=dict(parsed)))
        with pytest.raises(CatalogError):
            await manager.async_preview_mercadona_pdf(b"%PDF", reference, total, "owner")


async def test_mercadona_websocket_admin_only_and_no_secrets(hass, hass_ws_client, hass_read_only_user):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_TODO_ENTITY: "todo.bring"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    manager = hass.data[DOMAIN]["mercadona_account"]
    manager._mail = {"provider": "gmail", "host": "imap.gmail.com", "port": 993, "username": "t@example.test", "password": "SECRET", "folder": "INBOX"}
    manager._session["online"] = {"refresh_token": "SECRET_REFRESH", "customer_id": "cust-1"}
    assert hass.data[DOMAIN]["receipts"].ticket_accounts.accounts["mercadona"] is manager
    writer = await hass_ws_client(hass)
    await writer.send_json({"id": 1, "type": f"{DOMAIN}/mercadona_account/status"})
    result = await writer.receive_json()
    assert result["success"] and result["result"]["mail"]["connected"] and result["result"]["online"]["connected"]
    assert "SECRET" not in json.dumps(result)
    token = await hass.auth.async_create_refresh_token(hass_read_only_user, client_id="http://test.local")
    reader = await hass_ws_client(hass, hass.auth.async_create_access_token(token))
    actions = ("status", "login", "online_disconnect", "online_orders", "online_diagnostic", "mail_connect", "mail_disconnect",
               "disconnect", "options", "purchases", "sync_history", "preview", "preview_saved")
    fields = {"login": {"username": "a@example.test", "password": "x"}, "mail_connect": {"provider": "gmail", "username": "a@example.test", "password": "x"},
              "options": {"options": {"history_enabled": True, "auto_ticket": False}}, "preview": {"purchase_id": "id"}, "preview_saved": {"receipt_id": "id"}}
    for i, action in enumerate(actions, 1):
        await reader.send_json({"id": i, "type": f"{DOMAIN}/mercadona_account/{action}", **fields.get(action, {})})
        assert (await reader.receive_json())["error"]["code"] == "unauthorized"
    assert await hass.config_entries.async_unload(entry.entry_id)
