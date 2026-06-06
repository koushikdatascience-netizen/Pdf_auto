"""Compare a PDF-generated Mandai preview with an existing manually entered purchase."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

import db
from extract_pdf_json import extract_pdf
from integration_api.config import load_settings
from integration_api.mapping_store import MappingStore
from pdf_purchase_adapter import normalize_extracted_purchases
from purchase_service import preview_purchase


HEADER_FIELDS = ("suppliercode", "docno", "totamount", "tottaxOth", "totnetamt")
DETAIL_FIELDS = (
    "itemcode", "batchno", "itemrate", "itembox", "itemquantity", "itemamount",
    "itemboxrate", "itemmrp", "totalamount", "T1_Amt", "T2_Amt", "T3_Amt",
    "T4_Amt", "ETD",
)
TAX_FIELDS = ("TaxAccount", "OnAmount", "TaxAmount")


def normalized(value: Any) -> Any:
    if value is None:
        return None
    try:
        return str(Decimal(str(value)).normalize())
    except (InvalidOperation, ValueError):
        return str(value).strip()


def row_dicts(cursor: Any, sql: str, *params: Any) -> list[dict[str, Any]]:
    cursor.execute(sql, *params)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def compare_fields(
    area: str,
    manual: Mapping[str, Any],
    automated: Mapping[str, Any],
    fields: tuple[str, ...],
    differences: list[dict[str, Any]],
) -> None:
    for field in fields:
        manual_value = normalized(manual.get(field))
        automated_value = normalized(automated.get(field))
        if manual_value != automated_value:
            differences.append(
                {
                    "area": area,
                    "field": field,
                    "manual": manual_value,
                    "automated_preview": automated_value,
                }
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only manual-vs-PDF purchase comparison.")
    parser.add_argument("pdf_file", type=Path)
    parser.add_argument("--manual-trnno", type=int, required=True)
    parser.add_argument("--company", required=True)
    parser.add_argument("--year", required=True)
    parser.add_argument("--output", type=Path, default=Path("purchase_comparison.json"))
    args = parser.parse_args()

    settings = load_settings()
    invoices = normalize_extracted_purchases(extract_pdf(args.pdf_file.resolve()))
    mapping_config = MappingStore(settings.mapping_store_path).snapshot(settings.mapping_config)
    connection = db.connect(settings.connection_string)
    try:
        previews = [
            preview_purchase(
                invoice,
                connection,
                companycode=args.company,
                yearcode=args.year,
                mapping_config=mapping_config,
                supplier_lookup_sql=settings.supplier_lookup_sql,
                item_lookup_sql=settings.item_lookup_sql,
                item_code_verify_sql=settings.item_code_verify_sql,
                erp_profile=settings.erp_profile,
                erp_options=settings.erp_options,
            )
            for invoice in invoices
        ]
        if len(previews) != 1:
            raise RuntimeError(
                f"Comparison currently requires exactly one purchase preview; PDF produced {len(previews)}."
            )
        preview = previews[0]
        cursor = connection.cursor()
        manual_headers = row_dicts(
            cursor,
            "SELECT * FROM dbo.purchasemain WHERE companycode=? AND yearcode=? AND trnno=?",
            args.company,
            args.year,
            args.manual_trnno,
        )
        if len(manual_headers) != 1:
            raise RuntimeError(f"Expected one manual purchase header, found {len(manual_headers)}.")
        manual_details = row_dicts(
            cursor,
            """
            SELECT * FROM dbo.purchasedetail
            WHERE companycode=? AND yearcode=? AND trnno=?
            ORDER BY slno
            """,
            args.company,
            args.year,
            args.manual_trnno,
        )
        manual_tax = row_dicts(
            cursor,
            """
            SELECT * FROM dbo.PurchaseTaxDetail
            WHERE companycode=? AND yearcode=? AND trnno=?
            ORDER BY TaxAccount
            """,
            args.company,
            args.year,
            args.manual_trnno,
        )
        differences: list[dict[str, Any]] = []
        compare_fields("header", manual_headers[0], preview, HEADER_FIELDS, differences)
        if len(manual_details) != len(preview["items"]):
            differences.append(
                {
                    "area": "details",
                    "field": "row_count",
                    "manual": len(manual_details),
                    "automated_preview": len(preview["items"]),
                }
            )
        for index, (manual, automated) in enumerate(zip(manual_details, preview["items"]), start=1):
            compare_fields(f"detail[{index}]", manual, automated, DETAIL_FIELDS, differences)
        automated_tax = sorted(preview["tax_rows"], key=lambda row: str(row["TaxAccount"]))
        if len(manual_tax) != len(automated_tax):
            differences.append(
                {
                    "area": "tax",
                    "field": "row_count",
                    "manual": len(manual_tax),
                    "automated_preview": len(automated_tax),
                }
            )
        for index, (manual, automated) in enumerate(zip(manual_tax, automated_tax), start=1):
            compare_fields(f"tax[{index}]", manual, automated, TAX_FIELDS, differences)
        report = {
            "read_only": True,
            "pdf_file": args.pdf_file.name,
            "manual_trnno": args.manual_trnno,
            "matches": not differences,
            "difference_count": len(differences),
            "differences": differences,
            "automated_preview": preview,
        }
        args.output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"Created comparison report: {args.output}")
        print(f"Matches: {report['matches']}")
        print(f"Differences: {report['difference_count']}")
    finally:
        connection.rollback()
        connection.close()


if __name__ == "__main__":
    main()
