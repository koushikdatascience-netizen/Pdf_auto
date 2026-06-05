"""Validation and normalization for AI-extracted purchase invoice JSON."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, List, Mapping, Optional, Sequence


MONEY = Decimal("0.01")


class ValidationError(ValueError):
    """Raised when invoice JSON cannot safely be processed."""


@dataclass(frozen=True)
class ValidatedItem:
    item_name: str
    item_code: Optional[str]
    batch: str
    ml: Optional[Decimal]
    packing: Optional[str]
    strength_name: Optional[str]
    quantity: Decimal
    rate: Decimal
    amount: Decimal


@dataclass(frozen=True)
class ValidatedTax:
    code: str
    rate: Decimal
    amount: Decimal


@dataclass(frozen=True)
class ValidatedInvoice:
    supplier: str
    invoice_no: str
    date: datetime
    items: Sequence[ValidatedItem]
    tax: Optional[ValidatedTax]
    total: Decimal
    narration: Optional[str]
    warnings: Sequence[str]


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValidationError(f"Required field '{field}' is missing or empty.")
    return text


def _optional_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _decimal(value: Any, field: str, *, positive: bool = False) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValidationError(f"Field '{field}' must be numeric.")
    try:
        result = Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValidationError(f"Field '{field}' must be numeric.") from None
    if not result.is_finite():
        raise ValidationError(f"Field '{field}' must be finite.")
    if positive and result <= 0:
        raise ValidationError(f"Field '{field}' must be greater than zero.")
    if not positive and result < 0:
        raise ValidationError(f"Field '{field}' cannot be negative.")
    return result


def _date(value: Any) -> datetime:
    text = _required_text(value, "date")
    for date_format in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, date_format)
        except ValueError:
            pass
    raise ValidationError(
        "Field 'date' must use YYYY-MM-DD, YYYY-MM-DDTHH:MM:SS, or DD/MM/YYYY."
    )


def validate_invoice_json(
    json_data: Mapping[str, Any],
    *,
    strict_total: bool = False,
) -> ValidatedInvoice:
    """Validate and normalize invoice JSON before any database operation."""
    if not isinstance(json_data, Mapping):
        raise ValidationError("Invoice JSON must be an object.")

    supplier = _required_text(json_data.get("supplier"), "supplier")
    invoice_no = _required_text(json_data.get("invoice_no"), "invoice_no")
    invoice_date = _date(json_data.get("date"))
    total = _decimal(json_data.get("total"), "total")
    narration = _optional_text(json_data.get("narration"))

    raw_items = json_data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValidationError("Field 'items' must contain at least one line item.")

    items: List[ValidatedItem] = []
    for index, raw_item in enumerate(raw_items):
        field = f"items[{index}]"
        if not isinstance(raw_item, Mapping):
            raise ValidationError(f"Field '{field}' must be an object.")
        item_name = _required_text(raw_item.get("item_name"), f"{field}.item_name")
        item_code = _optional_text(raw_item.get("item_code"))
        batch = _required_text(raw_item.get("batch"), f"{field}.batch")
        ml = (
            _decimal(raw_item.get("ml"), f"{field}.ml", positive=True)
            if raw_item.get("ml") is not None
            else None
        )
        packing = _optional_text(raw_item.get("packing"))
        strength_name = _optional_text(raw_item.get("strength_name"))
        quantity = _decimal(raw_item.get("quantity"), f"{field}.quantity", positive=True)
        rate = _decimal(raw_item.get("rate"), f"{field}.rate")
        calculated_amount = (quantity * rate).quantize(MONEY, rounding=ROUND_HALF_UP)

        supplied_amount = raw_item.get("amount")
        if supplied_amount is not None:
            normalized_amount = _decimal(supplied_amount, f"{field}.amount")
            if normalized_amount != calculated_amount:
                raise ValidationError(
                    f"Field '{field}.amount' does not equal quantity * rate "
                    f"({calculated_amount})."
                )

        items.append(
            ValidatedItem(
                item_name=item_name,
                item_code=item_code,
                batch=batch,
                ml=ml,
                packing=packing,
                strength_name=strength_name,
                quantity=quantity,
                rate=rate,
                amount=calculated_amount,
            )
        )

    tax: Optional[ValidatedTax] = None
    raw_tax = json_data.get("tax")
    if raw_tax is not None:
        if not isinstance(raw_tax, Mapping):
            raise ValidationError("Field 'tax' must be an object when provided.")
        tax = ValidatedTax(
            code=_required_text(raw_tax.get("code"), "tax.code"),
            rate=_decimal(raw_tax.get("rate"), "tax.rate"),
            amount=_decimal(raw_tax.get("amount"), "tax.amount"),
        )

    items_total = sum((item.amount for item in items), Decimal("0.00"))
    tax_total = tax.amount if tax else Decimal("0.00")
    calculated_total = (items_total + tax_total).quantize(MONEY)
    warnings: List[str] = []
    if total != calculated_total:
        message = (
            f"Invoice total {total} differs from items plus tax {calculated_total}. "
            "This may represent charges, discounts, or extraction error."
        )
        if strict_total:
            raise ValidationError(message)
        warnings.append(message)

    return ValidatedInvoice(
        supplier=supplier,
        invoice_no=invoice_no,
        date=invoice_date,
        items=tuple(items),
        tax=tax,
        total=total,
        narration=narration,
        warnings=tuple(warnings),
    )
