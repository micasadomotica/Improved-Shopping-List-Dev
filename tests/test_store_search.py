"""Manual store search proposes product links; nothing is linked automatically."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.improved_shopping_list_dev import store_search as module
from custom_components.improved_shopping_list_dev.const import DOMAIN
from custom_components.improved_shopping_list_dev.store_search import (
    StoreSearch, StoreSearchError, clean_query, consum_candidates, mercadona_candidates)


MERCADONA = {"hits": [
    {"id": "22313", "display_name": "Yogur natural Hacendado", "brand": "Hacendado", "packaging": "Pack-6", "published": True,
     "share_url": "https://tienda.mercadona.es/product/22313/yogur-natural-hacendado-pack-6",
     "thumbnail": "https://prod-mercadona.imgix.net/images/a.jpg?fit=crop&h=300&w=300",
     "price_instructions": {"unit_price": "1.00", "unit_size": 0.75, "size_format": "kg", "reference_price": "1.334", "reference_format": "kg"}},
    {"id": "../evil", "display_name": "Bad"},
    {"id": "1", "display_name": "Hidden", "published": False},
    {"id": "20584", "display_name": "Otro", "share_url": "https://evil.example/product/20584", "thumbnail": "http://insecure/a.jpg",
     "price_instructions": {"unit_price": "x"}},
]}

CONSUM = {"catalog": {"products": [
    {"code": "7294387", "ean": "8410500001063", "productData": {
        "name": "Yogur Original Natural Pack de 2 Unidades", "brand": {"name": "DANONE"},
        "url": "https://tienda.consum.es/es/p/yogur-original-natural-pack-de-2-unidades/7294387",
        "imageURL": "https://cdn-consum.aktiosdigitalservices.com/a.jpg", "description": "Yogur Original Natural Pack de 2 Unidades 2 x 130 Gr"},
     "priceData": {"prices": [{"id": "PRICE", "value": {"centAmount": 1.99, "centUnitAmount": 7.65}}], "unitPriceUnitType": "1 Kg"}},
    {"code": "999", "productData": {"name": "Mismatch", "url": "https://tienda.consum.es/es/p/x/123"}},
    {"code": "5", "productData": {"name": "Outside", "url": "https://evil.example/es/p/x/5"}},
]}}


def test_mercadona_candidates_keep_only_safe_published_links():
    rows = mercadona_candidates(MERCADONA)
    assert [row["reference_id"] for row in rows] == ["22313", "20584"]
    first, second = rows
    assert first["url"] == "https://tienda.mercadona.es/product/22313/yogur-natural-hacendado-pack-6"
    assert first["format"] == "Pack-6 · 0,75 kg" and first["price"] == 1 and first["measure"] == "1,33 €/kg"
    assert first["image_url"].startswith("https://prod-mercadona.imgix.net/")
    # Foreign share links and non-HTTPS images are replaced by the verified product route.
    assert second["url"] == "https://tienda.mercadona.es/product/20584" and second["image_url"] == "" and second["price"] is None
    with pytest.raises(StoreSearchError):
        mercadona_candidates({"error": True})


def test_consum_candidates_require_matching_codes_and_store_hosts():
    rows = consum_candidates("consum", CONSUM)
    assert len(rows) == 1
    row = rows[0]
    assert row["reference_id"] == "7294387" and row["url"].endswith("/7294387")
    assert row["format"] == "2 x 130 Gr" and row["price"] == 1.99 and row["measure"] == "7,65 €/1 Kg" and row["brand"] == "DANONE"
    with pytest.raises(StoreSearchError):
        consum_candidates("consum", {"catalog": {}})


def test_query_is_bounded():
    assert clean_query("  yogur   natural ") == "yogur natural"
    for value in ("", "a", "x" * 101):
        with pytest.raises(StoreSearchError):
            clean_query(value)


def _search(hass, stores):
    hass.data[DOMAIN] = {"settings": SimpleNamespace(snapshot={"stores": stores})}
    return StoreSearch(hass)


async def test_searches_enabled_catalogs_and_offers_browser_links_for_protected_stores(hass):
    search = _search(hass, {"mercadona": {"enabled": True, "warehouse": "2752"}, "carrefour": {"enabled": True},
        "lidl": {"enabled": True}, "dia": {"enabled": True}, "consum": {"enabled": False}})
    assert search.stores() == (["mercadona"], ["carrefour", "lidl"])
    search._mercadona = AsyncMock(return_value=[{"reference_id": "1"}])
    search._pace = AsyncMock()
    result = await search.async_search(" leche  entera ")
    search._mercadona.assert_awaited_once_with("leche entera")
    assert result["results"] == [{"store": "mercadona", "store_name": "Mercadona", "candidates": [{"reference_id": "1"}]}]
    assert [link["url"] for link in result["browser"]] == ["https://www.carrefour.es/?q=leche+entera", "https://www.lidl.es/q/search?q=leche+entera"]


async def test_store_errors_are_reported_per_store_and_no_store_falls_back_to_mercadona(hass):
    search = _search(hass, {"consum": {"enabled": True}})
    assert search.stores() == (["consum"], [])
    search._pace = AsyncMock()
    search._consum = AsyncMock(side_effect=StoreSearchError("Consum ha rechazado la consulta (HTTP 403)."))
    result = await search.async_search("pan")
    assert result["results"][0]["candidates"] == [] and "HTTP 403" in result["results"][0]["error"]
    assert _search(hass, {}).stores() == (["mercadona"], [])


async def test_mercadona_search_uses_configured_warehouse(hass, monkeypatch):
    search = _search(hass, {"mercadona": {"enabled": True, "warehouse": "bad warehouse!"}})
    monkeypatch.setattr(module, "mercadona_location", lambda hass: {"warehouse": "bad warehouse!"})
    with pytest.raises(StoreSearchError):
        await search._mercadona("pan")


async def test_websocket_search_is_admin_only(hass, hass_ws_client, hass_read_only_user):
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from custom_components.improved_shopping_list_dev.const import CONF_TODO_ENTITY
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_TODO_ENTITY: "todo.shopping_list"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    search = AsyncMock(return_value={"query": "pan", "results": [{"store": "mercadona", "store_name": "Mercadona", "candidates": [], "error": "x"}], "browser": []})
    monkeypatch_target = StoreSearch(hass)
    monkeypatch_target.async_search = search
    hass.data[DOMAIN]["store_search"] = monkeypatch_target
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": f"{DOMAIN}/catalog/search_stores", "query": "pan"})
    response = await client.receive_json()
    assert response["success"] and response["result"]["query"] == "pan"
    search.assert_awaited_once_with("pan")
    token = await hass.auth.async_create_refresh_token(hass_read_only_user, client_id="http://test.local")
    reader = await hass_ws_client(hass, hass.auth.async_create_access_token(token))
    await reader.send_json({"id": 2, "type": f"{DOMAIN}/catalog/search_stores", "query": "pan"})
    assert (await reader.receive_json())["error"]["code"] == "unauthorized"
    assert search.await_count == 1
    assert await hass.config_entries.async_unload(entry.entry_id)
