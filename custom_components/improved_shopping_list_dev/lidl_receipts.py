"""Lidl printed HTML receipts: extract data only, never render provider HTML."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html.parser import HTMLParser
import json
import re

from homeassistant.util import dt as dt_util
from .catalog import CatalogError
from .receipt_parser import _identify_receipt


def amount(value):
    if type(value) not in (str, int, float):
        raise CatalogError("Lidl ha devuelto un importe no válido.", "lidl_ticket_format")
    try:
        text = str(value).strip().replace("€", "").strip()
        if "," in text:
            text = text.replace(".", "").replace(",", ".")
        result = Decimal(text)
    except InvalidOperation as err:
        raise CatalogError("Lidl ha devuelto un importe no válido.", "lidl_ticket_format") from err
    if not result.is_finite() or not 0 <= result <= 1000000:
        raise CatalogError("El importe de Lidl está fuera de rango.", "lidl_ticket_format")
    return result


def ticket_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_~.:%+=/-]{1,512}", value):
        raise CatalogError("La referencia del ticket de Lidl no es válida.", "lidl_ticket_identity")
    return value


def summary(raw):
    if not isinstance(raw, dict):
        raise CatalogError("Lidl ha devuelto un historial no reconocido.", "lidl_history_format")
    reference = ticket_id(raw.get("id"))
    date = raw.get("date")
    when = dt_util.parse_datetime(date) if isinstance(date, str) and len(date) <= 80 else None
    if when is None:
        raise CatalogError("La fecha de compra de Lidl no es válida.", "lidl_history_format")
    if when.tzinfo is None:
        # Lidl lists dates with Z but may omit that suffix in ticket details.
        # Interpret both consistently; never attach the Home Assistant local zone.
        when = when.replace(tzinfo=dt_util.UTC)
    return {"reference": reference, "purchased_at": dt_util.as_local(when).isoformat(),
            "amount": float(amount(raw.get("totalAmount"))), "supported": not raw.get("isDeleted", False)}


class PrintedReceipt(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.articles, self.text, self._row, self._depth = [], [], None, 0
        self.after = []  # printed text after each article, until the next one

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if tag == "span":
            if self._row is not None:
                self._depth += 1
            elif "article" in data.get("class", "").split():
                self._row = {"attributes": data, "text": ""}
                self.articles.append(self._row)
                self.after.append("")
                self._depth = 1
        if tag in ("br", "div", "p"):
            self.text.append("\n")
            if self._row is None and self.after:
                self.after[-1] += "\n"

    def handle_endtag(self, tag):
        if tag == "span" and self._row is not None:
            self._depth -= 1
            if self._depth == 0:
                self._row = None

    def handle_data(self, data):
        self.text.append(data)
        if self._row is not None:
            self._row["text"] += data
        elif self.after:
            self.after[-1] += data


DISCOUNT = re.compile(r"^\s*(?P<label>[^\d\n-][^\n]{0,120}?)\s+(?:-(?P<a>\d+[.,]\d{2})|(?P<b>\d+[.,]\d{2})-)\s*[A-Z]?\s*$")
TOTALS = re.compile(r"\b(?:total|subtotal|a pagar|importe|entregado|cambio|tarjeta|efectivo|iva|base)\b", re.I)


def _discounts(text):
    """Negative amounts printed right after an article (Lidl Plus, coupons, offers)."""
    result = []
    for raw in text.split("\n"):
        if TOTALS.search(raw):
            break
        match = DISCOUNT.match(raw)
        if match:
            result.append(amount(match["a"] or match["b"]))
    return result


def _apply_discounts(line, discounts):
    if not discounts:
        return
    gross = Decimal(str(line.get("gross_amount", line["amount"])))
    net = Decimal(str(line["amount"])) - sum(discounts, Decimal(0))
    if net < 0:
        raise CatalogError("El descuento de un artículo de Lidl no es válido.", "lidl_ticket_amount")
    line.update(amount=float(net), unit_price=float((net / line["count"]).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
        gross_amount=float(gross), line_discount=float(gross - net))


def _masked(line):
    """Diagnostics keep layout and amounts; long digit runs (cards, ids) are hidden."""
    line = re.sub(r"\d{4,}", lambda m: "#" * len(m[0]), re.sub(r"\s+", " ", line).strip())
    return line[:100]


def _amount_diagnostics(parser, lines, totals, expected):
    printed = [_masked(line) for line in "".join(parser.text).split("\n")
               if re.search(r"\d+[.,]\d{2}", line)]
    return {"articles": len(parser.articles), "lines": len(lines),
            "articles_sum": float(sum((Decimal(str(line["amount"])) for line in lines), Decimal(0))),
            "printed_totals": [float(amount(value)) for value in totals[-3:]], "expected_total": float(expected),
            "after_articles": [_masked(row) for text in parser.after for row in text.split("\n") if row.strip()][:60],
            "printed_amount_lines": printed[:80]}


def extract_lidl_ticket(detail, reference, expected_total, expected_date):
    if not isinstance(detail, dict) or detail.get("id") != ticket_id(reference) or detail.get("isDeleted"):
        raise CatalogError("El detalle de Lidl no corresponde a la compra.", "lidl_ticket_identity")
    parsed = summary({**detail, "totalAmount": expected_total})
    expected = dt_util.parse_datetime(expected_date)
    if expected is not None and expected.tzinfo is None:
        expected = expected.replace(tzinfo=dt_util.UTC)
    if expected is None or abs(dt_util.parse_datetime(parsed["purchased_at"]).timestamp() - expected.timestamp()) > 60:
        raise CatalogError("La fecha del ticket de Lidl no coincide.", "lidl_ticket_identity")
    html = detail.get("htmlPrintedReceipt")
    if not isinstance(html, str) or not 1 <= len(html) <= 1500000:
        raise CatalogError("Lidl no ha devuelto un ticket HTML reconocido.", "lidl_ticket_format")
    parser = PrintedReceipt(); parser.feed(html)
    if not 1 <= len(parser.articles) <= 600:
        raise CatalogError("No se reconocen los artículos de Lidl.", "lidl_ticket_format")
    lines = []; previous = None
    for index, row in enumerate(parser.articles):
        attrs, text = row["attributes"], row["text"]
        code, name = attrs.get("data-art-id", ""), attrs.get("data-art-description", "").strip()
        if not re.fullmatch(r"[0-9]{1,16}", code) or not 1 <= len(name) <= 500:
            raise CatalogError("No se reconoce una línea de Lidl.", "lidl_ticket_format")
        signature = tuple(attrs.get(k) for k in ("data-art-id", "data-art-description", "data-unit-price", "data-art-quantity"))
        # Weighted items have a second article span explaining kg × price/kg.
        if previous == signature and re.match(r"^\s*\d+(?:[.,]\d+)?\s*(?:kg|g|uds?)\s*[x×*]", text, re.I):
            _apply_discounts(lines[-1], _discounts(parser.after[index]))
            continue
        previous = signature
        quantity, price = amount(attrs.get("data-art-quantity", "1")), amount(attrs.get("data-unit-price"))
        amounts = re.findall(r"(?<![\d.,])-?\d+[.,]\d{2}(?!\d)", text)
        if not amounts or not quantity or quantity > 10000:
            raise CatalogError("No se reconocen las cantidades de Lidl.", "lidl_ticket_format")
        total = amount(amounts[-1])
        if abs((quantity * price).quantize(Decimal(".01"), rounding=ROUND_HALF_UP) - total) > Decimal(".02"):
            raise CatalogError("La cantidad y el precio de Lidl no coinciden.", "lidl_ticket_amount")
        following = parser.articles[index+1] if index+1 < len(parser.articles) else None
        weighted = quantity != int(quantity) or bool(following and following["attributes"].get("data-art-id") == code and re.search(r"\b(?:kg|g|uds?)\b", following["text"], re.I))
        count = 1 if weighted else int(quantity)
        pack = re.search(r"\b(\d+(?:[,.]\d+)?)\s*(kg|g|ml|cl|l)\b", name, re.I)
        line={"id": str(len(lines)), "description": name, "count": count,
            "unit_price": float(total / count), "amount": float(total), "reference_id": code, "ean": "",
            "pack_quantity": float(pack[1].replace(",", ".")) if pack and not weighted else None,
            "pack_unit": pack[2].lower() if pack and not weighted else ""}
        if weighted:
            # The purchased weight is not the content of a fixed package.
            unit="kg"
            if following and following["attributes"].get("data-art-id")==code:
                match=re.match(r"^\s*(\d+(?:[.,]\d+)?)\s*(kg|g|uds?)\s*[x×*]\s*(\d+(?:[.,]\d+)?)\s*(?:EUR|€)\s*/\s*(kg|g|uds?)",following["text"],re.I)
                if not match or match[2].lower()!=match[4].lower() or amount(match[1])!=quantity or amount(match[3])!=price:
                    raise CatalogError("El peso y el precio por unidad de Lidl no coinciden.", "lidl_ticket_amount")
                unit="uds" if match[2].lower() in ("ud","uds") else match[2].lower()
            line.update(purchased_quantity=float(quantity),purchased_unit=unit,quantity_estimated=False,
                ticket_measure_price={"price":float(price),"quantity":1,"unit":unit,"currency":"EUR",
                    "checked_at":dt_util.parse_datetime(parsed["purchased_at"]).timestamp(),"source":"ticket"})
        _apply_discounts(line, _discounts(parser.after[index]))
        lines.append(line)
    text = "".join(parser.text)
    totals = re.findall(r"(?:^|\n)\s*TOTAL\s+(\d+[.,]\d{2})(?:\s|$)", text, re.I)
    total = amount(expected_total); subtotal = sum((Decimal(str(line["amount"])) for line in lines), Decimal(0))
    if not totals or abs(amount(totals[-1]) - total) > Decimal(".02") or abs(subtotal-total) > Decimal(".02"):
        # Unknown discount/return layouts need a dedicated parser, never guessed prices.
        error = CatalogError("Los artículos no coinciden con el total de Lidl; se conserva la compra sin revisar.", "lidl_ticket_amount")
        error.diagnostics = _amount_diagnostics(parser, lines, totals, total)
        raise error
    gross = sum((Decimal(str(line.get("gross_amount", line["amount"]))) for line in lines), Decimal(0))
    receipt = {"store": "lidl", "store_name": "Lidl", "identity": reference, "purchased_at": parsed["purchased_at"],
        "currency": "EUR", "subtotal": float(gross), "discount": float(gross - subtotal), "shipping": 0, "total": float(total),
        "article_count": sum(line["count"] for line in lines), "lines": lines}
    return _identify_receipt(receipt, json.dumps(receipt, ensure_ascii=False, sort_keys=True).encode())
