"""Reviewable receipt reconciliation and conservative, per-store confirmation."""

import asyncio
from contextlib import AsyncExitStack
from copy import deepcopy
from uuid import uuid4

from homeassistant.exceptions import HomeAssistantError

from .catalog import CatalogError, normalized_name
from .quantity_values import positive_quantity
from .receipt_quantities import weight_purchase
from .todo_names import named_item


class ReceiptShopping:
    @staticmethod
    def _recorded_list_actions(receipt):
        """Freeze legacy product-level coverage before adding new links.

        Older actions did not retain line IDs. Conservatively cover the lines
        already linked to that product; never infer coverage from Stock alone.
        """
        actions = deepcopy(receipt.get("list_actions", []))
        for action in actions:
            if "line_ids" not in action:
                action["line_ids"] = [line["id"] for line in receipt.get("lines", [])
                    if line.get("product_id") == action.get("product_id") and line.get("confirmed") is not False]
        return actions

    @classmethod
    def _reviewed_list_lines(cls, receipt):
        # Includes explicit exclusions and products absent from the reviewed
        # list, so a later shopping trip is not reduced by an older receipt.
        return set(receipt.get("list_reviewed_lines", [])) | {
            identifier for action in cls._recorded_list_actions(receipt) for identifier in action["line_ids"]}

    def _save_list_review(self, draft, receipt, original):
        receipt["list_actions"] = self._recorded_list_actions(original) + draft.get("list_actions", [])
        receipt["list_pending"] = any(action["state"] not in ("applied", "skipped") for action in receipt["list_actions"])
        receipt["list_reviewed_lines"] = sorted(self._reviewed_list_lines(original) | set(draft.get("list_reviewed_lines", [])))

    @staticmethod
    def _receipt_amount(line, unit):
        from .receipts import converted_quantity

        # A weighed line may use count=1 even when it represents several kilos.
        if unit == "uds" and weight_purchase(line):
            return None
        return converted_quantity(line, unit)

    def confirmation_mode(self, store):
        settings = getattr(self, "settings", None)
        return settings.snapshot.get("receipt_confirmation", {}).get(store, "manual") if settings else "manual"

    async def _shopping_items(self, entity):
        quantities = self.automation.quantities
        state = self.hass.states.get(entity)
        if entity not in quantities.settings.snapshot["entities"] or state is None or state.state in ("unknown", "unavailable"):
            raise CatalogError("La lista no está disponible. Revisa la compra antes de confirmarla.")
        async with asyncio.timeout(30):
            result = await self.hass.services.async_call("todo", "get_items", {"entity_id": entity}, blocking=True, return_response=True)
        items = result.get(entity, {}).get("items") if isinstance(result, dict) else None
        if not isinstance(items, list):
            raise CatalogError("No se ha podido leer la lista de la compra.")
        return [named_item(quantities.settings, entity, item) for item in items]

    async def async_preview_list(self, token, owner, selections, entity=None):
        draft = self._draft(token, owner)
        original = draft.get("reprocess_original") or draft.get("partial_original") or {}
        if original.get("list_pending"):
            raise CatalogError("Resuelve primero la actualización pendiente de la Lista desde el detalle del ticket.")
        covered = self._reviewed_list_lines(original)
        links = {line["id"]: product["id"] if product else "" for line, product in self._links(draft["receipt"], selections)}
        quantities = getattr(self.automation, "quantities", None)
        settings = quantities.settings.snapshot if quantities else {"entities": [], "primary_entity": ""}
        entity = settings.get("primary_entity", "") if entity is None else entity
        entities = settings.get("entities", [])
        if entity and entity not in entities:
            raise CatalogError("Selecciona una lista configurada.")
        items = await self._shopping_items(entity) if entity else []
        groups = {}
        for line in draft["receipt"]["lines"]:
            if line["id"] in covered:
                continue
            product_id = links.get(line["id"], "")
            product = next((p for p in self.stock.catalog.products if p["id"] == product_id), None)
            if not product:
                continue
            matches = [item for item in items if item.get("status") == "needs_action" and normalized_name(item.get("summary", "")) == normalized_name(product["name"])]
            if not matches:
                continue
            group = groups.setdefault(product_id, {"product_id": product_id, "name": product["name"], "quantity": 0, "reason": "", "enabled": True, "line_ids": []})
            group["line_ids"].append(line["id"])
            if len(matches) != 1 or not matches[0].get("uid"):
                group.update(enabled=False, reason="Hay varias filas con este nombre o falta su identificador.")
                continue
            item = matches[0]
            value = quantities.value(entity, item, product)
            group.update(uid=str(item["uid"]), before=value["quantity"], unit=value["unit"], step=value["step"])
            amount = self._receipt_amount(line, value["unit"])
            if line.get("quantity_estimated") or amount is None or amount <= 0:
                group.update(enabled=False, reason="Revisa la cantidad comprada y su unidad.")
            else:
                group["quantity"] = round(group["quantity"] + amount, 3)
        review = {"id": str(uuid4()), "entity": entity, "entities": entities, "rows": list(groups.values()),
                  "links": deepcopy(selections), "covered_lines": len(covered)}
        draft["list_review"] = deepcopy(review)
        return review

    async def _list_actions(self, draft, selections, review):
        draft["list_reviewed_lines"] = []
        if review is None:
            return []  # Compatibility with clients predating the review step.
        expected = draft.get("list_review")
        if not isinstance(review, dict) or not expected or review.get("id") != expected["id"]:
            raise CatalogError("Vuelve a revisar las cantidades de la Lista.", "conflict")
        if not isinstance(selections, list) or any(not isinstance(line, dict) for line in selections):
            raise CatalogError("Las líneas seleccionadas no son válidas.")
        links = [{"id": line.get("id"), "product_id": line.get("product_id")} for line in selections]
        if links != expected["links"]:
            raise CatalogError("Han cambiado las vinculaciones. Vuelve a revisar la Lista.", "conflict")
        rows = review.get("rows")
        if not isinstance(rows, list) or len(rows) != len(expected["rows"]):
            raise CatalogError("Revisa todos los productos de la Lista.")
        items = await self._shopping_items(expected["entity"]) if expected["entity"] else []
        actions, seen = [], set()
        for row in rows:
            if not isinstance(row, dict) or type(row.get("enabled")) is not bool:
                raise CatalogError("La selección de la Lista no es válida.")
            base = next((r for r in expected["rows"] if r["product_id"] == row.get("product_id")), None)
            if not base or base["product_id"] in seen:
                raise CatalogError("La selección de la Lista no es válida.")
            seen.add(base["product_id"])
            if not row["enabled"]:
                continue
            product = next((p for p in self.stock.catalog.products if p["id"] == base["product_id"]), None)
            item = next((i for i in items if str(i.get("uid")) == base.get("uid")), None)
            if not product or not item or item.get("status") != "needs_action" or normalized_name(item.get("summary", "")) != normalized_name(product["name"]):
                raise CatalogError("La Lista ha cambiado. Vuelve a revisar la compra.", "conflict")
            current = self.automation.quantities.value(expected["entity"], item, product)
            if current["quantity"] != base["before"] or current["unit"] != base["unit"]:
                raise CatalogError("Las cantidades de la Lista han cambiado. Vuelve a revisarlas.", "conflict")
            try:
                amount = positive_quantity(row.get("quantity"))
            except ValueError as err:
                raise CatalogError("Indica una cantidad comprada positiva.") from err
            after = max(0, round(base["before"] - amount, 3))
            if not after and not int(self.hass.states.get(expected["entity"]).attributes.get("supported_features", 0)) & 4:
                raise CatalogError("La lista no permite marcar productos como comprados.")
            actions.append({**base, "id": str(uuid4()), "entity": expected["entity"], "quantity": amount, "after": after, "state": "pending"})
        draft["list_reviewed_lines"] = [link["id"] for link in expected["links"] if link["product_id"]]
        return actions

    async def async_confirm(self, token, owner, selections, update_prices, list_review=None):
        self._draft(token, owner)
        await self.automation.async_check()
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(self.automation._lock)
            if lock := getattr(self.automation, "_operation_lock", None):
                await stack.enter_async_context(lock)
            if quantities := getattr(self.automation, "quantities", None):
                await stack.enter_async_context(quantities._lock)
            draft = self._draft(token, owner)
            draft["list_actions"] = await self._list_actions(draft, selections, list_review)
            receipt = await self._async_confirm_purchase(token, owner, selections, update_prices)
            return await self._apply_list_actions(receipt)

    async def async_reprocess(self, token, owner, selections, list_review=None):
        self._draft(token, owner)
        await self.automation.async_check()
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(self.automation._lock)
            if lock := getattr(self.automation, "_operation_lock", None):
                await stack.enter_async_context(lock)
            if quantities := getattr(self.automation, "quantities", None):
                await stack.enter_async_context(quantities._lock)
            draft = self._draft(token, owner)
            if list_review is not None and draft.get("reprocess_original", {}).get("list_pending"):
                raise CatalogError("Resuelve primero la actualización pendiente de la Lista desde el detalle del ticket.")
            draft["list_actions"] = await self._list_actions(draft, selections, list_review)
            receipt = await self._async_reprocess_purchase(token, owner, selections)
            # Old clients may explicitly edit only formats/Stock without a list review.
            return await self._apply_list_actions(receipt) if list_review is not None else receipt

    async def _apply_list_actions(self, receipt):
        """Stock is committed first. A durable quantity ledger prevents repeat debits."""
        receipt = deepcopy(receipt)
        for action in receipt.get("list_actions", []):
            if action["state"] in ("applied", "skipped"):
                continue
            try:
                items = await self._shopping_items(action["entity"])
                item = next((i for i in items if str(i.get("uid")) == action["uid"]), None)
                await self.automation.quantities.async_apply_receipt_action(action, item)
                action.update(state="applied", error="")
            except (CatalogError, HomeAssistantError, OSError, TimeoutError) as err:
                action["error"] = str(err) or "No se ha podido comprobar la Lista."
        receipt["list_pending"] = any(a["state"] not in ("applied", "skipped") for a in receipt.get("list_actions", []))
        if receipt.get("list_actions"):
            async with self.stock._lock:
                data = deepcopy(self.stock.receipts)
                current = data["history"][receipt["id"]]
                current.update(list_actions=receipt["list_actions"], list_pending=receipt["list_pending"])
                await self.stock._persist(deepcopy(self.stock._items), receipts=data)
                receipt = deepcopy(current)
        return receipt

    async def async_retry_list(self, identifier, owner, keep_current=None):
        async with self.automation._lock, self.automation._operation_lock, self.automation.quantities._lock:
            receipt = deepcopy(self.stock.receipts["history"].get(identifier))
            if not receipt:
                raise CatalogError("La compra ya no está disponible.")
            if keep_current is not None:
                action = next((row for row in receipt.get("list_actions", []) if row["id"] == keep_current), None)
                if not action:
                    raise CatalogError("La actualización pendiente ya no existe.")
                if action["state"] not in ("applied", "skipped"):
                    action.update(state="skipped", error="")
            return await self._apply_list_actions(receipt)

    async def async_apply_confirmation_policy(self, preview, owner):
        """Called on new uploads/downloads, never just by opening saved history."""
        if self.confirmation_mode(preview["receipt"]["store"]) != "automatic":
            return preview
        selections = []
        for line in preview["receipt"]["lines"]:
            product = next((p for p in preview["products"] if p["id"] == line.get("product_id")), None)
            amount = self._receipt_amount(line, product["unit"]) if product else None
            safe = bool(product and line.get("matched") and not line.get("quantity_estimated") and amount is not None and amount > 0)
            selections.append({"id": line["id"], "product_id": product["id"] if safe else "", "quantity": amount if safe else None,
                               "unit": product["unit"] if safe else "uds", "replenish": safe, "purchase_id": "", "defer": not safe})
        review = await self.async_preview_list(preview["token"], owner, [{"id": s["id"], "product_id": s["product_id"]} for s in selections])
        # An ambiguous list match/quantity keeps its receipt lines pending too.
        unsafe = {row["product_id"] for row in review["rows"] if not row["enabled"]}
        if unsafe:
            for selection in selections:
                if selection["product_id"] in unsafe:
                    selection.update(product_id="", replenish=False, quantity=None, defer=True)
            review = await self.async_preview_list(preview["token"], owner, [{"id": s["id"], "product_id": s["product_id"]} for s in selections])
        receipt = await self.async_confirm(preview["token"], owner, selections, False, review)
        return {"automatic": True, "receipt": receipt}
