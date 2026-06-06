"""High-level purchase preview and transactional insertion service."""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
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


def _profile_invoice_data(
    json_data: Mapping[str, Any],
    erp_profile: str,
    erp_options: Mapping[str, Any],
) -> Mapping[str, Any]:
    if erp_profile != "mandai":
        return json_data
    supplier_override = str(erp_options.get("supplier_name_override", "")).strip()
    if not supplier_override:
        return json_data
    prepared = dict(json_data)
    prepared["supplier"] = supplier_override
    source = dict(prepared.get("erp_source") or {})
    source["pdf_supplier"] = json_data.get("supplier")
    prepared["erp_source"] = source
    return prepared


def _mandai_payload(
    json_data: Mapping[str, Any],
    invoice: ValidatedInvoice,
    resolved: ResolvedInvoice,
    cursor: Any,
    companycode: str,
    yearcode: str,
    options: Mapping[str, Any],
) -> Dict[str, Any]:
    source = json_data.get("erp_source")
    if not isinstance(source, Mapping):
        raise PurchaseServiceError("Mandai profile requires PDF erp_source metadata.")
    db.validate_mandai_context(cursor, companycode, dict(options))
    raw_items = json_data.get("items")
    if not isinstance(raw_items, list) or len(raw_items) != len(resolved.items):
        raise PurchaseServiceError("Mandai source item metadata is incomplete.")
    income_tax = Decimal(str(source.get("income_tax", 0))).quantize(Decimal("0.01"))
    details = []
    total_t3 = Decimal("0.00")
    total_t4 = Decimal("0.00")
    total_duty = Decimal("0.00")
    for line_number, (item, raw_item) in enumerate(zip(resolved.items, raw_items), start=1):
        master = db.get_mandai_item_master(cursor, item.item_code)
        cases = item.source.quantity
        packing = Decimal(master["packing"])
        bottles = (cases * packing).quantize(Decimal("1"))
        box_rate = (item.source.amount / cases).quantize(Decimal("0.01"), ROUND_HALF_UP)
        bottle_rate = (item.source.amount / bottles).quantize(Decimal("0.01"), ROUND_HALF_UP)
        vat = Decimal(str(raw_item.get("vat", 0))).quantize(Decimal("0.01"))
        duty = Decimal(str(raw_item.get("duty", 0))).quantize(Decimal("0.01"))
        t3 = (cases * Decimal(str(master["t3_amount_per_case"]))).quantize(
            Decimal("0.01"), ROUND_HALF_UP
        )
        t4 = (cases * Decimal(str(master["t4_amount_per_case"]))).quantize(
            Decimal("0.01"), ROUND_HALF_UP
        )
        total_t3 += t3
        total_t4 += t4
        total_duty += duty
        details.append(
            {
                "slno": line_number,
                "itemcode": item.item_code,
                "batchno": item.source.batch,
                "itemrate": bottle_rate,
                "itembox": int(cases),
                "itemloose": 0,
                "itemquantity": int(bottles),
                "itemamount": item.source.amount,
                "itemfreeqnty": 0,
                "itemboxrate": box_rate,
                "itemmrp": Decimal(str(master["mrp"])).quantize(Decimal("0.01")),
                "itemdiscount": Decimal("0.00"),
                "totalamount": item.source.amount,
                "T1_Amt": vat,
                "T2_Amt": Decimal("0.00"),
                "T3_Amt": t3,
                "T4_Amt": t4,
                "ETD": duty,
            }
        )
    if details:
        allocated = Decimal("0.00")
        for index, detail in enumerate(details):
            if index == len(details) - 1:
                detail["T2_Amt"] = income_tax - allocated
            else:
                share = (income_tax * detail["itemamount"] / sum(d["itemamount"] for d in details)).quantize(
                    Decimal("0.01"), ROUND_HALF_UP
                )
                detail["T2_Amt"] = share
                allocated += share
    item_total = sum((detail["itemamount"] for detail in details), Decimal("0.00"))
    vat_total = sum((detail["T1_Amt"] for detail in details), Decimal("0.00"))
    net_before_rounding = item_total + vat_total + income_tax + total_t3 + total_t4
    net_total = net_before_rounding.quantize(Decimal("1"), ROUND_HALF_UP)
    rounding = net_total - net_before_rounding
    return {
        "profile": "mandai",
        "companycode": companycode,
        "yearcode": yearcode,
        "supplier": invoice.supplier,
        "pdf_supplier": source.get("pdf_supplier"),
        "suppliercode": resolved.supplier_code,
        "docno": str(source.get("docno") or invoice.invoice_no),
        "trndate": invoice.date.isoformat(),
        "docdate": invoice.date.isoformat(),
        "tppassno": str(source.get("tppassno") or options.get("tppassno_default", "")),
        "ptype": options.get("ptype", "PURCHASE"),
        "purchaseacccode": options.get("purchaseacccode", "P00002"),
        "shopcode": options.get("shopcode", "S00001"),
        "checkedBy": options.get("checked_by", "A00001"),
        "billType": options.get("bill_type", "AI"),
        "schemecode": "",
        "totamount": str(net_before_rounding),
        "tottaxOth": str(rounding),
        "totnetamt": str(net_total),
        "narration": invoice.narration or "",
        "items": [{key: str(value) if isinstance(value, Decimal) else value for key, value in d.items()} for d in details],
        "tax_rows": [
            {
                "TaxAccount": options.get("rounding_account", "IEX001"),
                "OnAmount": "0.00",
                "TaxAmount": str(rounding),
            },
            {
                "TaxAccount": options.get("purchase_tax_account", "E00001"),
                "OnAmount": str(net_before_rounding),
                "TaxAmount": "0.00",
            },
        ],
        "source_totals": {
            "manufacturing_amount": str(item_total),
            "vat": str(vat_total),
            "income_tax": str(income_tax),
            "t3": str(total_t3),
            "t4": str(total_t4),
            "duty": str(total_duty),
        },
        "warnings": (["Mandai tppassno is blank; confirm before insert."] if not (source.get("tppassno") or options.get("tppassno_default")) else []),
    }


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
    erp_profile: str = "generic",
    erp_options: Optional[Mapping[str, Any]] = None,
) -> list[Dict[str, Any]]:
    """Return every unresolved ERP master reference without stopping at the first."""
    prepared = _profile_invoice_data(json_data, erp_profile, erp_options or {})
    invoice = validate_invoice_json(prepared, strict_total=strict_total)
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
    erp_profile: str = "generic",
    erp_options: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Validate and resolve master codes without performing inserts."""
    db.validate_company_year(companycode, yearcode)
    prepared = _profile_invoice_data(json_data, erp_profile, erp_options or {})
    invoice = validate_invoice_json(prepared, strict_total=strict_total)
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
        if erp_profile == "mandai":
            return _mandai_payload(prepared, invoice, resolved, cursor, companycode, yearcode, erp_options or {})
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
    erp_profile: str = "generic",
    erp_options: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Insert one invoice atomically and return generated trnid/trnno."""
    db.validate_transaction_context(
        companycode,
        yearcode,
        transaction_type,
        usercode,
        sync,
    )
    prepared = _profile_invoice_data(json_data, erp_profile, erp_options or {})
    invoice = validate_invoice_json(prepared, strict_total=strict_total)
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
        mandai = (
            _mandai_payload(prepared, invoice, resolved, cursor, companycode, yearcode, erp_options or {})
            if erp_profile == "mandai"
            else None
        )
        db.ensure_invoice_not_duplicate(
            cursor,
            companycode,
            yearcode,
            resolved.supplier_code,
            mandai["docno"] if mandai else invoice.invoice_no,
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
        if mandai:
            db.insert_mandai_purchase_main(
                cursor,
                (
                    companycode, yearcode, trnid, trnno, invoice.date, mandai["ptype"],
                    mandai["purchaseacccode"], resolved.supplier_code, mandai["shopcode"],
                    mandai["docno"], invoice.date, mandai["tppassno"], "", mandai["totamount"],
                    mandai["tottaxOth"], mandai["totnetamt"], mandai["narration"],
                    mandai["checkedBy"], False, mandai["billType"], sync,
                ),
            )
            db.insert_mandai_purchase_details(
                cursor,
                [
                    (
                        companycode, yearcode, trnno, d["slno"], d["itemcode"], d["batchno"],
                        d["itemrate"], d["itembox"], d["itemloose"], d["itemquantity"],
                        d["itemamount"], d["itemfreeqnty"], d["itemboxrate"], d["itemmrp"],
                        d["itemdiscount"], invoice.date, 0, 0, 0, 0, d["totalamount"],
                        d["T1_Amt"], d["T2_Amt"], d["T3_Amt"], d["T4_Amt"], d["ETD"],
                    )
                    for d in mandai["items"]
                ],
            )
            tax_rows = db.insert_mandai_purchase_tax(
                cursor,
                [
                    (
                        companycode, trnno, None, "", 0, row["OnAmount"],
                        row["TaxAmount"], row["TaxAccount"], yearcode,
                    )
                    for row in mandai["tax_rows"]
                ],
            )
        else:
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
            "warnings": list(invoice.warnings) + (list(mandai["warnings"]) if mandai else []),
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
