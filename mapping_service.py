"""Configurable supplier and item mapping for ERP purchase integration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from db import DatabaseError, lookup_single_value
from validation import ValidatedInvoice, ValidatedItem


DEFAULT_SUPPLIER_LOOKUP_SQL = """
SELECT ledgerCode
FROM dbo.MasterAccountsLedger
WHERE ledgerName = ? AND companyCode = ?
""".strip()

DEFAULT_ITEM_LOOKUP_SQL = """
SELECT itemcode
FROM dbo.itemmst
WHERE itemname = ?
  AND (? IS NULL OR ml = ?)
  AND (? IS NULL OR packing = ?)
  AND (? IS NULL OR strengthname = ?)
""".strip()

DEFAULT_ITEM_CODE_VERIFY_SQL = """
SELECT itemcode
FROM dbo.itemmst
WHERE itemcode = ?
""".strip()


class MappingError(DatabaseError):
    """Raised when supplier or item mapping cannot be resolved uniquely."""


@dataclass(frozen=True)
class MappingConfig:
    supplier_aliases: Mapping[str, str]
    item_mappings: Mapping[str, str]


@dataclass(frozen=True)
class ResolvedItem:
    source: ValidatedItem
    item_code: str


@dataclass(frozen=True)
class ResolvedInvoice:
    source: ValidatedInvoice
    supplier_code: str
    items: tuple[ResolvedItem, ...]


def load_mapping_config(path: Optional[Path]) -> MappingConfig:
    """Load optional supplier aliases and item mappings from JSON."""
    if path is None:
        return MappingConfig(supplier_aliases={}, item_mappings={})
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise MappingError("Mapping configuration must be a JSON object.")
    supplier_aliases = data.get("supplier_aliases", {})
    item_mappings = data.get("item_mappings", {})
    if not isinstance(supplier_aliases, Mapping) or not isinstance(item_mappings, Mapping):
        raise MappingError("'supplier_aliases' and 'item_mappings' must be JSON objects.")
    return MappingConfig(
        supplier_aliases=dict(supplier_aliases),
        item_mappings=dict(item_mappings),
    )


def item_mapping_key(item: ValidatedItem) -> str:
    """Stable item mapping key combining item name and batch."""
    return f"{item.item_name}|{item.batch}"


def resolve_supplier_code(
    cursor: Any,
    supplier_name: str,
    companycode: str,
    config: MappingConfig,
    *,
    supplier_lookup_sql: str = DEFAULT_SUPPLIER_LOOKUP_SQL,
) -> str:
    """Resolve supplier alias or name through the ERP supplier master."""
    lookup_name = str(config.supplier_aliases.get(supplier_name) or supplier_name).strip()
    try:
        return lookup_single_value(
            cursor,
            supplier_lookup_sql,
            (lookup_name, companycode),
            f"Supplier '{lookup_name}'",
        )
    except DatabaseError as exc:
        raise MappingError(str(exc)) from exc


def resolve_item_code(
    cursor: Any,
    item: ValidatedItem,
    companycode: str,
    config: MappingConfig,
    *,
    item_lookup_sql: str = DEFAULT_ITEM_LOOKUP_SQL,
    item_code_verify_sql: str = DEFAULT_ITEM_CODE_VERIFY_SQL,
) -> str:
    """Resolve and always verify the final item code exists in ERP item master."""
    candidate_code = item.item_code
    mapped_code = str(config.item_mappings.get(item_mapping_key(item)) or "").strip()
    if not candidate_code and mapped_code:
        candidate_code = mapped_code

    try:
        if not candidate_code:
            candidate_code = lookup_single_value(
                cursor,
                item_lookup_sql,
                (
                    item.item_name,
                    item.ml,
                    item.ml,
                    item.packing,
                    item.packing,
                    item.strength_name,
                    item.strength_name,
                ),
                f"Item name '{item.item_name}'",
            )
        verified_code = lookup_single_value(
            cursor,
            item_code_verify_sql,
            (candidate_code,),
            f"Item code '{candidate_code}'",
        )
        return verified_code
    except DatabaseError as exc:
        raise MappingError(str(exc)) from exc


def resolve_invoice(
    cursor: Any,
    invoice: ValidatedInvoice,
    companycode: str,
    config: MappingConfig,
    *,
    supplier_lookup_sql: str = DEFAULT_SUPPLIER_LOOKUP_SQL,
    item_lookup_sql: str = DEFAULT_ITEM_LOOKUP_SQL,
    item_code_verify_sql: str = DEFAULT_ITEM_CODE_VERIFY_SQL,
) -> ResolvedInvoice:
    """Resolve every required ERP master code before any insert begins."""
    supplier_code = resolve_supplier_code(
        cursor,
        invoice.supplier,
        companycode,
        config,
        supplier_lookup_sql=supplier_lookup_sql,
    )
    items = tuple(
        ResolvedItem(
            source=item,
            item_code=resolve_item_code(
                cursor,
                item,
                companycode,
                config,
                item_lookup_sql=item_lookup_sql,
                item_code_verify_sql=item_code_verify_sql,
            ),
        )
        for item in invoice.items
    )
    return ResolvedInvoice(source=invoice, supplier_code=supplier_code, items=items)
