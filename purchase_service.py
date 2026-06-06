"""High-level purchase preview and transactional insertion service."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Mapping, Optional

import db
from mapping_service import (
    DEFAULT_ITEM_CODE_VERIFY_SQL,
    DEFAULT_ITEM_LOOKUP_SQL,
    DEFAULT_SUPPLIER_LOOKUP_SQL,
    MappingConfig,
    MappingError,
    ResolvedInvoice,
    item_mapping_key,
    resolve_item_code,
    resolve_invoice,
    resolve_supplier_code,
)
from validation import ValidatedInvoice, validate_invoice_json


LOGGER = logging.getLogger(__name__)


class PurchaseServiceError(RuntimeError):
    """Raised when preview or insertion fails."""


def inspect_purchase_mappings(
    json_data: Mapping[str, Any],
    connection: Any,
    *,
    companycode: str,
    mapping_config: Optional[MappingConfig] = None,
    supplier_lookup_sql: str = DEFAULT_SUPPLIER_LOOKUP_SQL,
    item_lookup_sql: str = DEFAULT_ITEM_LOOKUP_SQL,
    item_code_verify_sql: str = DEFAULT_ITEM_CODE_VERIFY_SQL,
    strict_total: bool = False,
) -> list[Dict[str, Any]]:
    """Return every unresolved ERP master reference without stopping at the first."""
    invoice = validate_invoice_json(json_data, strict_total=strict_total)
    config = mapping_config or MappingConfig({}, {})
    cursor = connection.cursor()
    issues: list[Dict[str, Any]] = []
    try:
        try:
            resolve_supplier_code(
                cursor,
                invoice.supplier,
                companycode,
                config,
                supplier_lookup_sql=supplier_lookup_sql,
            )
        except MappingError as exc:
            issues.append(
                {
                    "type": "supplier",
                    "source": invoice.supplier,
                    "mapping_key": None,
                    "message": str(exc),
                }
            )

        for item in invoice.items:
            try:
                resolve_item_code(
                    cursor,
                    item,
                    companycode,
                    config,
                    item_lookup_sql=item_lookup_sql,
                    item_code_verify_sql=item_code_verify_sql,
                )
            except MappingError as exc:
                issues.append(
                    {
                        "type": "item",
                        "source": item.item_name,
                        "mapping_key": item_mapping_key(item),
                        "batch": item.batch,
                        "ml": str(item.ml) if item.ml is not None else None,
                        "packing": item.packing,
                        "strength_name": item.strength_name,
                        "message": str(exc),
                    }
                )
        return issues
    finally:
        cursor.close()
        connection.rollback()


def _preview_payload(
    invoice: ValidatedInvoice,
    resolved: ResolvedInvoice,
    companycode: str,
    yearcode: str,
) -> Dict[str, Any]:
    return {
        "companycode": companycode,
        "yearcode": yearcode,
        "supplier": invoice.supplier,
        "suppliercode": resolved.supplier_code,
        "docno": invoice.invoice_no,
        "trndate": invoice.date.isoformat(),
        "totamount": str(invoice.total),
        "totnetamt": str(invoice.total),
        "narration": invoice.narration,
        "items": [
            {
                "item_name": item.source.item_name,
                "itemcode": item.item_code,
                "batchno": item.source.batch,
                "ml": str(item.source.ml) if item.source.ml is not None else None,
                "packing": item.source.packing,
                "strength_name": item.source.strength_name,
                "itemquantity": str(item.source.quantity),
                "itemrate": str(item.source.rate),
                "itemamount": str(item.source.amount),
            }
            for item in resolved.items
        ],
        "tax": (
            {
                "TaxCode": invoice.tax.code,
                "TaxRate": str(invoice.tax.rate),
                "TaxAmount": str(invoice.tax.amount),
            }
            if invoice.tax
            else None
        ),
        "warnings": list(invoice.warnings),
    }


def preview_purchase(
    json_data: Mapping[str, Any],
    connection: Any,
    *,
    companycode: str,
    yearcode: str,
    mapping_config: Optional[MappingConfig] = None,
    supplier_lookup_sql: str = DEFAULT_SUPPLIER_LOOKUP_SQL,
    item_lookup_sql: str = DEFAULT_ITEM_LOOKUP_SQL,
    item_code_verify_sql: str = DEFAULT_ITEM_CODE_VERIFY_SQL,
    strict_total: bool = False,
) -> Dict[str, Any]:
    """Validate and resolve master codes without performing inserts."""
    db.validate_company_year(companycode, yearcode)
    invoice = validate_invoice_json(json_data, strict_total=strict_total)
    config = mapping_config or MappingConfig({}, {})
    cursor = connection.cursor()
    try:
        resolved = resolve_invoice(
            cursor,
            invoice,
            companycode,
            config,
            supplier_lookup_sql=supplier_lookup_sql,
            item_lookup_sql=item_lookup_sql,
            item_code_verify_sql=item_code_verify_sql,
        )
        return _preview_payload(invoice, resolved, companycode, yearcode)
    finally:
        cursor.close()
        connection.rollback()


def insert_purchase(
    json_data: Mapping[str, Any],
    connection: Any,
    *,
    companycode: str,
    yearcode: str,
    mapping_config: Optional[MappingConfig] = None,
    supplier_lookup_sql: str = DEFAULT_SUPPLIER_LOOKUP_SQL,
    item_lookup_sql: str = DEFAULT_ITEM_LOOKUP_SQL,
    item_code_verify_sql: str = DEFAULT_ITEM_CODE_VERIFY_SQL,
    strict_total: bool = False,
    transaction_type: str = "Purchase_Add",
    usercode: str = "A00001",
    sync: str = "N",
) -> Dict[str, Any]:
    """Insert one invoice atomically and return generated trnid/trnno."""
    db.validate_transaction_context(
        companycode,
        yearcode,
        transaction_type,
        usercode,
        sync,
    )
    invoice = validate_invoice_json(json_data, strict_total=strict_total)
    config = mapping_config or MappingConfig({}, {})
    cursor = None
    original_autocommit = bool(getattr(connection, "autocommit", False))
    try:
        if original_autocommit:
            connection.autocommit = False
        cursor = connection.cursor()
        cursor.execute("SET XACT_ABORT ON")
        cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")

        resolved = resolve_invoice(
            cursor,
            invoice,
            companycode,
            config,
            supplier_lookup_sql=supplier_lookup_sql,
            item_lookup_sql=item_lookup_sql,
            item_code_verify_sql=item_code_verify_sql,
        )
        db.ensure_invoice_not_duplicate(
            cursor,
            companycode,
            yearcode,
            resolved.supplier_code,
            invoice.invoice_no,
        )
        trnid, trnno = db.get_next_ids(cursor, companycode, yearcode)
        LOGGER.info(
            "Inserting invoice %s using trnid=%s trnno=%s.",
            invoice.invoice_no,
            trnid,
            trnno,
        )

        db.insert_transaction_master(
            cursor,
            companycode=companycode,
            yearcode=yearcode,
            trnid=trnid,
            transaction_type=transaction_type,
            usercode=usercode,
            transaction_date=datetime.now(),
            sync=sync,
        )
        db.insert_purchase_main(
            cursor,
            (
                companycode,
                yearcode,
                trnid,
                trnno,
                invoice.date,
                resolved.supplier_code,
                invoice.invoice_no,
                invoice.total,
                invoice.total,
                invoice.narration,
            ),
        )
        db.insert_purchase_details(
            cursor,
            [
                (
                    companycode,
                    yearcode,
                    trnid,
                    trnno,
                    line_number,
                    item.item_code,
                    item.source.batch,
                    item.source.rate,
                    item.source.quantity,
                    item.source.amount,
                    invoice.date,
                )
                for line_number, item in enumerate(resolved.items, start=1)
            ],
        )
        tax_rows = db.insert_purchase_tax(
            cursor,
            (
                companycode,
                yearcode,
                trnno,
                invoice.tax.code,
                invoice.tax.rate,
                invoice.tax.amount,
            )
            if invoice.tax
            else None,
        )

        connection.commit()
        LOGGER.info("Committed invoice %s.", invoice.invoice_no)
        return {
            "trnid": trnid,
            "trnno": trnno,
            "suppliercode": resolved.supplier_code,
            "detail_rows_inserted": len(resolved.items),
            "tax_rows_inserted": tax_rows,
            "transaction_master_inserted": True,
            "warnings": list(invoice.warnings),
        }
    except Exception as exc:
        connection.rollback()
        LOGGER.exception("Invoice insertion failed; transaction rolled back.")
        if isinstance(exc, (db.DatabaseError, PurchaseServiceError)):
            raise
        raise PurchaseServiceError(str(exc)) from exc
    finally:
        if cursor is not None:
            cursor.close()
        if original_autocommit:
            connection.autocommit = True
