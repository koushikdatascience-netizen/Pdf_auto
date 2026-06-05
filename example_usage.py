"""Example CLI for previewing or inserting purchase invoice JSON."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

import db
from mapping_service import (
    DEFAULT_ITEM_CODE_VERIFY_SQL,
    DEFAULT_ITEM_LOOKUP_SQL,
    DEFAULT_SUPPLIER_LOOKUP_SQL,
    load_mapping_config,
)
from purchase_service import insert_purchase, preview_purchase


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview or insert an ERP purchase invoice.")
    parser.add_argument("invoice_json", type=Path)
    parser.add_argument("--company", required=True)
    parser.add_argument("--year", required=True)
    parser.add_argument("--mapping-config", type=Path)
    parser.add_argument("--insert", action="store_true", help="Insert after validation and mapping.")
    parser.add_argument("--strict-total", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    connection_string = os.environ.get("SQLSERVER_CONNECTION_STRING")
    if not connection_string:
        raise SystemExit("Set SQLSERVER_CONNECTION_STRING before running.")

    invoice = json.loads(args.invoice_json.read_text(encoding="utf-8"))
    mappings = load_mapping_config(args.mapping_config)
    supplier_lookup_sql = os.environ.get(
        "ERP_SUPPLIER_LOOKUP_SQL",
        DEFAULT_SUPPLIER_LOOKUP_SQL,
    )
    item_lookup_sql = os.environ.get(
        "ERP_ITEM_LOOKUP_SQL",
        DEFAULT_ITEM_LOOKUP_SQL,
    )
    item_code_verify_sql = os.environ.get(
        "ERP_ITEM_CODE_VERIFY_SQL",
        DEFAULT_ITEM_CODE_VERIFY_SQL,
    )
    connection = db.connect(connection_string)
    try:
        if args.insert:
            output = insert_purchase(
                invoice,
                connection,
                companycode=args.company,
                yearcode=args.year,
                mapping_config=mappings,
                supplier_lookup_sql=supplier_lookup_sql,
                item_lookup_sql=item_lookup_sql,
                item_code_verify_sql=item_code_verify_sql,
                strict_total=args.strict_total,
                transaction_type=os.environ.get(
                    "ERP_PURCHASE_TRANSACTION_TYPE", "Purchase_Add"
                ),
                usercode=os.environ.get("ERP_PURCHASE_USERCODE", "A00001"),
                sync=os.environ.get("ERP_PURCHASE_SYNC", "N"),
            )
        else:
            output = preview_purchase(
                invoice,
                connection,
                companycode=args.company,
                yearcode=args.year,
                mapping_config=mappings,
                supplier_lookup_sql=supplier_lookup_sql,
                item_lookup_sql=item_lookup_sql,
                item_code_verify_sql=item_code_verify_sql,
                strict_total=args.strict_total,
            )
        print(json.dumps(output, indent=2))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
