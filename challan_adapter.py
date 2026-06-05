"""Adapt extracted excise challan JSON into ERP purchase payloads.

One challan may contain multiple manufacturers. Because one purchasemain row has
one supplier, this adapter creates one purchase payload per manufacturer.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from purchase_db import (
    DEFAULT_SUPPLIER_LOOKUP_SQL,
    PurchaseValidationError,
    create_connection,
    insert_purchase_batch,
    validate_json,
)


LOGGER = logging.getLogger(__name__)
MONEY = Decimal("0.01")

DEFAULT_ITEM_LOOKUP_SQL = """
SELECT itemcode
FROM dbo.itemmaster
WHERE itemname = ?
  AND capacity_ml = ?
  AND package = ?
  AND companycode = ?
""".strip()


class AdapterError(Exception):
    """Base error for challan-to-purchase adaptation."""


class ItemNotFoundError(AdapterError):
    """No ERP item code matched a challan product."""


class ItemLookupError(AdapterError):
    """Item lookup was ambiguous or unsafe."""


def _text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise AdapterError(f"Required extracted field '{field}' is missing.")
    return result


def _money(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise AdapterError(f"Extracted field '{field}' must be numeric.") from None
    if result < 0:
        raise AdapterError(f"Extracted field '{field}' cannot be negative.")
    return result


def _positive_int(value: Any, field: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise AdapterError(f"Extracted field '{field}' must be a positive integer.") from None
    if result <= 0:
        raise AdapterError(f"Extracted field '{field}' must be a positive integer.")
    return result


def _iso_date(value: Any) -> str:
    text = _text(value, "header.date")
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    raise AdapterError("Extracted header date must use DD/MM/YYYY or YYYY-MM-DD.")


def _validate_select_sql(sql: str, lookup_name: str) -> None:
    blocked = re.compile(
        r"\b(INSERT|UPDATE|DELETE|MERGE|EXEC|EXECUTE|DROP|ALTER|CREATE|TRUNCATE)\b",
        re.IGNORECASE,
    )
    if not sql.lstrip().upper().startswith("SELECT") or ";" in sql or blocked.search(sql):
        raise ItemLookupError(f"{lookup_name} SQL must be one SELECT statement.")


def item_override_key(item: Mapping[str, Any]) -> str:
    """Stable key suitable for an explicit item-code override JSON file."""
    return "|".join(
        [
            _text(item.get("label_name"), "items[].label_name"),
            str(_positive_int(item.get("capacity_ml"), "items[].capacity_ml")),
            _text(item.get("package"), "items[].package"),
            _text(item.get("batch"), "items[].batch"),
        ]
    )


def get_item_code(
    cursor: Any,
    item: Mapping[str, Any],
    companycode: str,
    *,
    item_lookup_sql: str = DEFAULT_ITEM_LOOKUP_SQL,
) -> str:
    """Resolve one extracted item to exactly one ERP itemcode."""
    _validate_select_sql(item_lookup_sql, "Item lookup")
    label_name = _text(item.get("label_name"), "items[].label_name")
    capacity_ml = _positive_int(item.get("capacity_ml"), "items[].capacity_ml")
    package = _text(item.get("package"), "items[].package")
    cursor.execute(item_lookup_sql, label_name, capacity_ml, package, companycode)
    rows = cursor.fetchmany(2)
    if not rows:
        raise ItemNotFoundError(
            f"No ERP item found for '{label_name}', {capacity_ml}ml, {package}."
        )
    if len(rows) > 1:
        raise ItemLookupError(
            f"Multiple ERP items matched '{label_name}', {capacity_ml}ml, {package}."
        )
    return _text(rows[0][0], "itemcode")


def adapt_challan(
    extracted_json: Mapping[str, Any],
    *,
    companycode: str,
    yearcode: str,
    item_resolver: Callable[[Mapping[str, Any]], str],
    supplier_aliases: Optional[Mapping[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Create validated purchase payloads, one per manufacturer."""
    if not isinstance(extracted_json, Mapping):
        raise AdapterError("Extracted challan JSON must be an object.")
    header = extracted_json.get("header")
    raw_items = extracted_json.get("items")
    groups = extracted_json.get("manufacturer_groups")
    if not isinstance(header, Mapping):
        raise AdapterError("Extracted challan header is missing.")
    if not isinstance(raw_items, list) or not raw_items:
        raise AdapterError("Extracted challan contains no items.")
    if not isinstance(groups, list) or not groups:
        raise AdapterError("Extracted challan contains no manufacturer groups.")

    demand_id = _text(header.get("demand_id"), "header.demand_id")
    date = _iso_date(header.get("date"))
    items_by_sl = {_positive_int(item.get("sl"), "items[].sl"): item for item in raw_items}
    aliases = dict(supplier_aliases or {})
    purchases: List[Dict[str, Any]] = []

    for group_index, group in enumerate(groups, start=1):
        manufacturer = _text(group.get("manufacturer"), "manufacturer_groups[].manufacturer")
        supplier = str(aliases.get(manufacturer) or manufacturer).strip()
        serials = group.get("item_sl_numbers")
        if not isinstance(serials, list) or not serials:
            raise AdapterError(f"Manufacturer group {group_index} has no item serial numbers.")

        purchase_items: List[Dict[str, Any]] = []
        source_mfg_total = _money(group.get("total_mfg_amount"), "group.total_mfg_amount")
        source_vat_total = _money(group.get("total_vat"), "group.total_vat")
        source_duty_total = _money(group.get("total_duty"), "group.total_duty")

        for serial in serials:
            serial_no = _positive_int(serial, "manufacturer_groups[].item_sl_numbers[]")
            item = items_by_sl.get(serial_no)
            if item is None:
                raise AdapterError(f"Manufacturer group references missing item serial {serial_no}.")
            if _text(item.get("manufacturer"), f"items[{serial_no}].manufacturer") != manufacturer:
                raise AdapterError(f"Item serial {serial_no} manufacturer does not match its group.")

            quantity = _positive_int(item.get("quantity_cases"), f"items[{serial_no}].quantity_cases")
            amount = _money(item.get("mfg_amount"), f"items[{serial_no}].mfg_amount")
            rate = (amount / quantity).quantize(MONEY, rounding=ROUND_HALF_UP)
            calculated = (rate * quantity).quantize(MONEY, rounding=ROUND_HALF_UP)
            if calculated != amount:
                raise AdapterError(
                    f"Item serial {serial_no} amount {amount} cannot be represented exactly "
                    f"as quantity {quantity} times itemrate."
                )

            itemcode = _text(item_resolver(item), f"items[{serial_no}].itemcode")
            purchase_items.append(
                {
                    "name": itemcode,
                    "qty": quantity,
                    "price": str(rate),
                    "amount": str(amount),
                    "batch": _text(item.get("batch"), f"items[{serial_no}].batch"),
                }
            )

        mapped_mfg_total = sum(
            (_money(item["amount"], "mapped item amount") for item in purchase_items),
            Decimal("0.00"),
        )
        if mapped_mfg_total != source_mfg_total:
            raise AdapterError(
                f"Manufacturer group {group_index} item amount total {mapped_mfg_total} "
                f"does not match source total {source_mfg_total}."
            )

        effective_vat_rate = (
            (source_vat_total / source_mfg_total * Decimal("100")).quantize(MONEY)
            if source_mfg_total
            else Decimal("0.00")
        )
        total = (source_mfg_total + source_vat_total).quantize(MONEY)
        payload = {
            "companycode": companycode,
            "yearcode": yearcode,
            "supplier": supplier,
            "invoice_no": f"{demand_id}-{group_index:02d}",
            "date": date,
            "items": purchase_items,
            "tax": {
                "tax_code": "VAT",
                "gst_rate": str(effective_vat_rate),
                "gst_amount": str(source_vat_total),
            },
            "total": str(total),
            "adapter_audit": {
                "source_demand_id": demand_id,
                "source_manufacturer": manufacturer,
                "source_item_sl_numbers": list(serials),
                "source_duty_excluded": str(source_duty_total),
                "source_mfg_amount": str(source_mfg_total),
                "source_vat": str(source_vat_total),
            },
        }
        validate_json(payload, validate_total=True)
        purchases.append(payload)

    grouped_serials = sorted(
        serial for group in groups for serial in group.get("item_sl_numbers", [])
    )
    source_serials = sorted(items_by_sl)
    if grouped_serials != source_serials:
        raise AdapterError("Manufacturer groups do not cover every item exactly once.")
    return purchases


def adapt_challan_with_db(
    extracted_json: Mapping[str, Any],
    connection: Any,
    *,
    companycode: str,
    yearcode: str,
    item_lookup_sql: str = DEFAULT_ITEM_LOOKUP_SQL,
    item_overrides: Optional[Mapping[str, str]] = None,
    supplier_aliases: Optional[Mapping[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Adapt a challan using ERP item-master lookups without inserting data."""
    cursor = connection.cursor()
    overrides = dict(item_overrides or {})
    try:
        def resolver(item: Mapping[str, Any]) -> str:
            override = overrides.get(item_override_key(item))
            if override:
                return override
            return get_item_code(
                cursor,
                item,
                companycode,
                item_lookup_sql=item_lookup_sql,
            )

        return adapt_challan(
            extracted_json,
            companycode=companycode,
            yearcode=yearcode,
            item_resolver=resolver,
            supplier_aliases=supplier_aliases,
        )
    finally:
        cursor.close()


def adapt_and_insert_challan(
    extracted_json: Mapping[str, Any],
    connection: Any,
    *,
    companycode: str,
    yearcode: str,
    item_lookup_sql: str = DEFAULT_ITEM_LOOKUP_SQL,
    supplier_lookup_sql: str = DEFAULT_SUPPLIER_LOOKUP_SQL,
    item_overrides: Optional[Mapping[str, str]] = None,
    supplier_aliases: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Adapt and insert every manufacturer purchase in one transaction."""
    try:
        purchases = adapt_challan_with_db(
            extracted_json,
            connection,
            companycode=companycode,
            yearcode=yearcode,
            item_lookup_sql=item_lookup_sql,
            item_overrides=item_overrides,
            supplier_aliases=supplier_aliases,
        )
        results = insert_purchase_batch(
            purchases,
            connection,
            supplier_lookup_sql=supplier_lookup_sql,
        )
        return {"purchase_count": len(results), "purchases": results}
    except Exception:
        connection.rollback()
        raise


def _load_mapping_config(path: Optional[Path]) -> Dict[str, Dict[str, str]]:
    if path is None:
        return {"item_overrides": {}, "supplier_aliases": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "item_overrides": dict(data.get("item_overrides", {})),
        "supplier_aliases": dict(data.get("supplier_aliases", {})),
    }


def build_mapping_template(extracted_json: Mapping[str, Any]) -> Dict[str, Dict[str, str]]:
    """Build a fill-in template containing every supplier and item mapping needed."""
    groups = extracted_json.get("manufacturer_groups")
    items = extracted_json.get("items")
    if not isinstance(groups, list) or not isinstance(items, list):
        raise AdapterError("Extracted JSON must contain manufacturer_groups and items.")
    return {
        "supplier_aliases": {
            _text(group.get("manufacturer"), "manufacturer_groups[].manufacturer"): ""
            for group in groups
        },
        "item_overrides": {item_override_key(item): "" for item in items},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Adapt extracted challan JSON for ERP insertion.")
    parser.add_argument("extracted_json", type=Path)
    parser.add_argument("--company", required=True)
    parser.add_argument("--year", required=True)
    parser.add_argument("--mapping-config", type=Path)
    parser.add_argument("--preview", action="store_true", help="Print adapted JSON without inserts.")
    parser.add_argument(
        "--export-mapping-template",
        type=Path,
        help="Write required supplier/item mapping keys and exit without connecting.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    extracted = json.loads(args.extracted_json.read_text(encoding="utf-8"))
    if args.export_mapping_template:
        template = build_mapping_template(extracted)
        args.export_mapping_template.write_text(
            json.dumps(template, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"Wrote mapping template: {args.export_mapping_template}")
        return

    config = _load_mapping_config(args.mapping_config)
    connection_string = os.environ.get("SQLSERVER_CONNECTION_STRING")
    if not connection_string:
        raise SystemExit("Set SQLSERVER_CONNECTION_STRING for ERP item and supplier lookups.")

    connection = create_connection(connection_string)
    try:
        if args.preview:
            output = adapt_challan_with_db(
                extracted,
                connection,
                companycode=args.company,
                yearcode=args.year,
                item_overrides=config["item_overrides"],
                supplier_aliases=config["supplier_aliases"],
            )
            connection.rollback()
        else:
            output = adapt_and_insert_challan(
                extracted,
                connection,
                companycode=args.company,
                yearcode=args.year,
                item_overrides=config["item_overrides"],
                supplier_aliases=config["supplier_aliases"],
            )
        print(json.dumps(output, indent=2))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
