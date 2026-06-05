"""Convert structured PDF extraction output into normalized purchase invoices."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Mapping

from validation import validate_invoice_json


MONEY = Decimal("0.01")


class PdfPurchaseAdapterError(ValueError):
    """Raised when extracted PDF data cannot become a safe purchase invoice."""


def _text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise PdfPurchaseAdapterError(f"Extracted field '{field}' is missing.")
    return result


def _money(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise PdfPurchaseAdapterError(f"Extracted field '{field}' must be numeric.") from None
    if result < 0:
        raise PdfPurchaseAdapterError(f"Extracted field '{field}' cannot be negative.")
    return result


def _positive_money(value: Any, field: str) -> Decimal:
    result = _money(value, field)
    if result <= 0:
        raise PdfPurchaseAdapterError(f"Extracted field '{field}' must be greater than zero.")
    return result


def _iso_date(value: Any) -> str:
    text = _text(value, "header.date")
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    raise PdfPurchaseAdapterError("Extracted PDF date must use DD/MM/YYYY or YYYY-MM-DD.")


def normalize_extracted_purchases(extracted: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Return one normalized purchase invoice per extracted manufacturer group."""
    if not isinstance(extracted, Mapping):
        raise PdfPurchaseAdapterError("PDF extraction result must be an object.")
    header = extracted.get("header")
    items = extracted.get("items")
    groups = extracted.get("manufacturer_groups")
    if not isinstance(header, Mapping):
        raise PdfPurchaseAdapterError("PDF extraction did not produce a document header.")
    if not isinstance(items, list) or not items:
        raise PdfPurchaseAdapterError("PDF extraction did not produce any product rows.")
    if not isinstance(groups, list) or not groups:
        raise PdfPurchaseAdapterError("PDF extraction did not associate products with suppliers.")

    demand_id = _text(header.get("demand_id"), "header.demand_id")
    invoice_date = _iso_date(header.get("date"))
    items_by_serial: Dict[int, Mapping[str, Any]] = {}
    for item in items:
        if not isinstance(item, Mapping):
            raise PdfPurchaseAdapterError("Every extracted product row must be an object.")
        try:
            serial = int(item.get("sl"))
        except (TypeError, ValueError):
            raise PdfPurchaseAdapterError("Every extracted product row requires a numeric serial.") from None
        if serial <= 0 or serial in items_by_serial:
            raise PdfPurchaseAdapterError("Extracted product serials must be positive and unique.")
        items_by_serial[serial] = item

    normalized: List[Dict[str, Any]] = []
    covered_serials: List[int] = []
    for group_index, group in enumerate(groups, start=1):
        if not isinstance(group, Mapping):
            raise PdfPurchaseAdapterError("Every manufacturer group must be an object.")
        supplier = _text(group.get("manufacturer"), "manufacturer_groups[].manufacturer")
        serials = group.get("item_sl_numbers")
        if not isinstance(serials, list) or not serials:
            raise PdfPurchaseAdapterError(f"Manufacturer group {group_index} contains no products.")

        normalized_items: List[Dict[str, Any]] = []
        calculated_items_total = Decimal("0.00")
        for raw_serial in serials:
            try:
                serial = int(raw_serial)
            except (TypeError, ValueError):
                raise PdfPurchaseAdapterError("Manufacturer product serial must be numeric.") from None
            item = items_by_serial.get(serial)
            if item is None:
                raise PdfPurchaseAdapterError(
                    f"Manufacturer group {group_index} references missing product serial {serial}."
                )
            if _text(item.get("manufacturer"), f"items[{serial}].manufacturer") != supplier:
                raise PdfPurchaseAdapterError(
                    f"Product serial {serial} is associated with the wrong manufacturer."
                )

            quantity = _positive_money(
                item.get("quantity_cases"), f"items[{serial}].quantity_cases"
            )
            amount = _money(item.get("mfg_amount"), f"items[{serial}].mfg_amount")
            rate = (amount / quantity).quantize(MONEY, rounding=ROUND_HALF_UP)
            if (rate * quantity).quantize(MONEY) != amount:
                raise PdfPurchaseAdapterError(
                    f"Product serial {serial} amount cannot be represented accurately as quantity x rate."
                )
            normalized_items.append(
                {
                    "item_name": _text(item.get("label_name"), f"items[{serial}].label_name"),
                    "batch": _text(item.get("batch"), f"items[{serial}].batch"),
                    "ml": item.get("capacity_ml"),
                    "quantity": str(quantity),
                    "rate": str(rate),
                    "amount": str(amount),
                }
            )
            calculated_items_total += amount
            covered_serials.append(serial)

        source_items_total = _money(group.get("total_mfg_amount"), "group.total_mfg_amount")
        if calculated_items_total.quantize(MONEY) != source_items_total:
            raise PdfPurchaseAdapterError(
                f"Manufacturer group {group_index} product total does not match its extracted total."
            )
        tax_amount = _money(group.get("total_vat"), "group.total_vat")
        tax_rate = (
            (tax_amount / source_items_total * Decimal("100")).quantize(MONEY)
            if source_items_total
            else Decimal("0.00")
        )
        invoice = {
            "supplier": supplier,
            "invoice_no": f"{demand_id}-{group_index:02d}",
            "date": invoice_date,
            "narration": f"Imported from PDF demand {demand_id}",
            "items": normalized_items,
            "tax": {"code": "VAT", "rate": str(tax_rate), "amount": str(tax_amount)},
            "total": str((source_items_total + tax_amount).quantize(MONEY)),
        }
        validate_invoice_json(invoice, strict_total=True)
        normalized.append(invoice)

    if sorted(covered_serials) != sorted(items_by_serial):
        raise PdfPurchaseAdapterError(
            "Manufacturer groups must associate every extracted product exactly once."
        )
    return normalized
