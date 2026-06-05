"""Read-only compatibility check for the live ERP SQL Server database."""

from __future__ import annotations

import json
import os
import argparse
from pathlib import Path
from typing import Any, Dict, List

import db


EXPECTED_INSERT_COLUMNS = {
    "trnidmst": [
        "companycode",
        "yearcode",
        "trnid",
        "trn_type",
        "usercode",
        "TranDate",
        "Sync",
    ],
    "purchasemain": [
        "companycode",
        "yearcode",
        "trnid",
        "trnno",
        "trndate",
        "suppliercode",
        "docno",
        "totamount",
        "totnetamt",
        "narration",
    ],
    "purchasedetail": [
        "companycode",
        "yearcode",
        "trnid",
        "trnno",
        "itemcode",
        "batchno",
        "itemrate",
        "itemquantity",
        "itemamount",
        "trndate",
    ],
    "PurchaseTaxDetail": [
        "companycode",
        "yearcode",
        "trnno",
        "TaxCode",
        "TaxRate",
        "TaxAmount",
    ],
}

FORBIDDEN_WRITE_TABLES = {
    "transactionmain",
    "transactiondetail",
    "transactionmatch",
}


def inspect_table(cursor: Any, table: str) -> List[Dict[str, Any]]:
    cursor.execute(
        """
        SELECT
            c.COLUMN_NAME,
            c.DATA_TYPE,
            c.IS_NULLABLE,
            c.COLUMN_DEFAULT,
            COLUMNPROPERTY(
                OBJECT_ID(QUOTENAME(c.TABLE_SCHEMA) + '.' + QUOTENAME(c.TABLE_NAME)),
                c.COLUMN_NAME,
                'IsIdentity'
            ) AS IS_IDENTITY
        FROM INFORMATION_SCHEMA.COLUMNS c
        WHERE c.TABLE_SCHEMA = 'dbo' AND c.TABLE_NAME = ?
        ORDER BY c.ORDINAL_POSITION
        """,
        table,
    )
    return [
        {
            "name": row[0],
            "type": row[1],
            "nullable": row[2] == "YES",
            "default": row[3],
            "identity": bool(row[4]),
        }
        for row in cursor.fetchall()
    ]


def inspect_foreign_keys(cursor: Any, table: str) -> List[Dict[str, str]]:
    cursor.execute(
        """
        SELECT
            fk.name,
            COL_NAME(fkc.parent_object_id, fkc.parent_column_id),
            OBJECT_SCHEMA_NAME(fkc.referenced_object_id),
            OBJECT_NAME(fkc.referenced_object_id),
            COL_NAME(fkc.referenced_object_id, fkc.referenced_column_id)
        FROM sys.foreign_keys fk
        JOIN sys.foreign_key_columns fkc
          ON fkc.constraint_object_id = fk.object_id
        WHERE fk.parent_object_id = OBJECT_ID(?)
        ORDER BY fk.name
        """,
        f"dbo.{table}",
    )
    return [
        {
            "constraint": row[0],
            "column": row[1],
            "referenced_schema": row[2],
            "referenced_table": row[3],
            "referenced_column": row[4],
        }
        for row in cursor.fetchall()
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Check ERP purchase schema compatibility.")
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the JSON report directly using UTF-8 encoding.",
    )
    args = parser.parse_args()

    connection_string = os.environ.get("SQLSERVER_CONNECTION_STRING")
    if not connection_string:
        raise SystemExit("Set SQLSERVER_CONNECTION_STRING first.")

    connection = db.connect(connection_string)
    cursor = connection.cursor()
    try:
        report: Dict[str, Any] = {"tables": {}, "blocking_issues": []}
        for table, inserted_columns in EXPECTED_INSERT_COLUMNS.items():
            columns = inspect_table(cursor, table)
            actual_by_lower = {column["name"].lower(): column for column in columns}
            missing_insert_columns = [
                column for column in inserted_columns if column.lower() not in actual_by_lower
            ]
            required_not_inserted = [
                column["name"]
                for column in columns
                if not column["nullable"]
                and column["default"] is None
                and not column["identity"]
                and column["name"].lower()
                not in {name.lower() for name in inserted_columns}
            ]
            report["tables"][table] = {
                "missing_insert_columns": missing_insert_columns,
                "required_columns_not_inserted": required_not_inserted,
                "foreign_keys": inspect_foreign_keys(cursor, table),
                "columns": columns,
            }
            if missing_insert_columns:
                report["blocking_issues"].append(
                    f"{table} is missing expected columns: {', '.join(missing_insert_columns)}"
                )
            if required_not_inserted:
                report["blocking_issues"].append(
                    f"{table} has required columns not handled by the service: "
                    f"{', '.join(required_not_inserted)}"
                )
            forbidden_dependencies = [
                foreign_key
                for foreign_key in report["tables"][table]["foreign_keys"]
                if foreign_key["referenced_table"].lower() in FORBIDDEN_WRITE_TABLES
            ]
            for foreign_key in forbidden_dependencies:
                report["blocking_issues"].append(
                    f"{table}.{foreign_key['column']} requires an existing "
                    f"{foreign_key['referenced_table']}.{foreign_key['referenced_column']} "
                    f"through {foreign_key['constraint']}; automatic ID generation must "
                    "match the ERP's existing master-data rule."
                )

        report["compatible"] = not report["blocking_issues"]
        report_json = json.dumps(report, indent=2, default=str)
        if args.output:
            args.output.write_text(report_json, encoding="utf-8")
            print(f"Wrote schema report: {args.output}")
        else:
            print(report_json)
    finally:
        cursor.close()
        connection.rollback()
        connection.close()


if __name__ == "__main__":
    main()
