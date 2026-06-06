"""Read-only compatibility check for the live ERP SQL Server database."""

from __future__ import annotations

import json
import os
import argparse
from datetime import datetime, timezone
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
        "slno",
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

MANDAI_INSERT_COLUMNS = {
    **EXPECTED_INSERT_COLUMNS,
    "purchasemain": [
        "companycode", "yearcode", "trnid", "trnno", "trndate", "ptype",
        "purchaseacccode", "suppliercode", "shopcode", "docno", "docdate",
        "tppassno", "schemecode", "totamount", "tottaxOth", "totnetamt",
        "narration", "checkedBy", "saletax_including_free", "billType", "Sync",
    ],
    "purchasedetail": [
        "companycode", "yearcode", "trnno", "slno", "itemcode", "batchno",
        "itemrate", "itembox", "itemloose", "itemquantity", "itemamount",
        "itemfreeqnty", "itemboxrate", "itemmrp", "itemdiscount", "trndate",
        "cgst", "sgst", "cess", "addcess", "totalamount", "T1_Amt", "T2_Amt",
        "T3_Amt", "T4_Amt", "ETD",
    ],
    "PurchaseTaxDetail": [
        "companycode", "trnno", "schemecode", "TaxCode", "TaxRate", "OnAmount",
        "TaxAmount", "TaxAccount", "yearcode",
    ],
}

REQUIRED_MASTER_COLUMNS = {
    "MasterAccountsLedger": ["companyCode", "ledgerCode", "ledgerName"],
    "itemmst": ["itemcode", "itemname", "ml", "packing", "strengthname"],
}

MANDAI_MASTER_COLUMNS = {
    **REQUIRED_MASTER_COLUMNS,
    "itemmst": [
        "itemcode", "itemname", "ml", "packing", "strengthname", "MRP",
        "T3_Amt", "T4_Amt",
    ],
    "storage": ["companyCode", "shopcode"],
}


def expected_insert_columns(profile: str) -> Dict[str, List[str]]:
    return MANDAI_INSERT_COLUMNS if profile.lower() == "mandai" else EXPECTED_INSERT_COLUMNS


def required_master_columns(profile: str) -> Dict[str, List[str]]:
    return MANDAI_MASTER_COLUMNS if profile.lower() == "mandai" else REQUIRED_MASTER_COLUMNS

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
            c.CHARACTER_MAXIMUM_LENGTH,
            c.NUMERIC_PRECISION,
            c.NUMERIC_SCALE,
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
            "max_length": row[2],
            "precision": row[3],
            "scale": row[4],
            "nullable": row[5] == "YES",
            "default": row[6],
            "identity": bool(row[7]),
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


def inspect_inbound_foreign_keys(cursor: Any, table: str) -> List[Dict[str, str]]:
    cursor.execute(
        """
        SELECT
            fk.name,
            OBJECT_SCHEMA_NAME(fkc.parent_object_id),
            OBJECT_NAME(fkc.parent_object_id),
            COL_NAME(fkc.parent_object_id, fkc.parent_column_id),
            COL_NAME(fkc.referenced_object_id, fkc.referenced_column_id)
        FROM sys.foreign_keys fk
        JOIN sys.foreign_key_columns fkc
          ON fkc.constraint_object_id = fk.object_id
        WHERE fkc.referenced_object_id = OBJECT_ID(?)
        ORDER BY fk.name
        """,
        f"dbo.{table}",
    )
    return [
        {
            "constraint": row[0],
            "referencing_schema": row[1],
            "referencing_table": row[2],
            "referencing_column": row[3],
            "referenced_column": row[4],
        }
        for row in cursor.fetchall()
    ]


def inspect_indexes(cursor: Any, table: str) -> List[Dict[str, Any]]:
    cursor.execute(
        """
        SELECT
            i.name,
            i.is_primary_key,
            i.is_unique,
            i.type_desc,
            COL_NAME(ic.object_id, ic.column_id),
            ic.key_ordinal
        FROM sys.indexes i
        JOIN sys.index_columns ic
          ON ic.object_id = i.object_id AND ic.index_id = i.index_id
        WHERE i.object_id = OBJECT_ID(?) AND ic.is_included_column = 0
        ORDER BY i.index_id, ic.key_ordinal
        """,
        f"dbo.{table}",
    )
    indexes: Dict[str, Dict[str, Any]] = {}
    for row in cursor.fetchall():
        entry = indexes.setdefault(
            row[0],
            {
                "name": row[0],
                "primary_key": bool(row[1]),
                "unique": bool(row[2]),
                "type": row[3],
                "columns": [],
            },
        )
        entry["columns"].append(row[4])
    return list(indexes.values())


def inspect_checks(cursor: Any, table: str) -> List[Dict[str, str]]:
    cursor.execute(
        """
        SELECT name, definition
        FROM sys.check_constraints
        WHERE parent_object_id = OBJECT_ID(?)
        ORDER BY name
        """,
        f"dbo.{table}",
    )
    return [{"name": row[0], "definition": row[1]} for row in cursor.fetchall()]


def inspect_triggers(cursor: Any, table: str) -> List[Dict[str, Any]]:
    cursor.execute(
        """
        SELECT name, is_disabled, is_instead_of_trigger
        FROM sys.triggers
        WHERE parent_id = OBJECT_ID(?)
        ORDER BY name
        """,
        f"dbo.{table}",
    )
    return [
        {"name": row[0], "disabled": bool(row[1]), "instead_of": bool(row[2])}
        for row in cursor.fetchall()
    ]


def inspect_referencing_modules(cursor: Any, table: str) -> List[Dict[str, str]]:
    cursor.execute(
        """
        SELECT DISTINCT
            OBJECT_SCHEMA_NAME(m.object_id),
            OBJECT_NAME(m.object_id),
            o.type_desc
        FROM sys.sql_modules m
        JOIN sys.objects o ON o.object_id = m.object_id
        WHERE m.definition LIKE ?
        ORDER BY OBJECT_NAME(m.object_id)
        """,
        f"%{table}%",
    )
    return [
        {"schema": row[0], "name": row[1], "type": row[2]}
        for row in cursor.fetchall()
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Check ERP purchase schema compatibility.")
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the JSON report directly using UTF-8 encoding.",
    )
    parser.add_argument(
        "--include-module-scan",
        action="store_true",
        help="Also scan stored procedure/view definitions. This can be slow on large ERPs.",
    )
    parser.add_argument(
        "--profile",
        choices=("generic", "mandai"),
        default=os.environ.get("ERP_PROFILE", "generic").lower(),
        help="Validate the columns used by the selected ERP insert profile.",
    )
    args = parser.parse_args()

    connection_string = os.environ.get("SQLSERVER_CONNECTION_STRING")
    if not connection_string:
        raise SystemExit("Set SQLSERVER_CONNECTION_STRING first.")

    connection = db.connect(connection_string)
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT DB_NAME(), @@SERVERNAME, CAST(SERVERPROPERTY('ProductVersion') AS varchar(100))")
        identity = cursor.fetchone()
        report: Dict[str, Any] = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "read_only_audit": True,
            "database": {"name": identity[0], "server": identity[1], "sql_version": identity[2]},
            "tables": {},
            "master_tables": {},
            "blocking_issues": [],
            "manual_review": [],
        }
        report["erp_profile"] = args.profile
        for table, inserted_columns in expected_insert_columns(args.profile).items():
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
                "inbound_foreign_keys": inspect_inbound_foreign_keys(cursor, table),
                "indexes": inspect_indexes(cursor, table),
                "check_constraints": inspect_checks(cursor, table),
                "triggers": inspect_triggers(cursor, table),
                "referencing_modules": (
                    inspect_referencing_modules(cursor, table)
                    if args.include_module_scan
                    else []
                ),
                "columns": columns,
            }
            if not columns:
                report["blocking_issues"].append(f"Required table dbo.{table} does not exist.")
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
            if report["tables"][table]["triggers"]:
                report["manual_review"].append(
                    f"Review triggers on dbo.{table}: "
                    + ", ".join(trigger["name"] for trigger in report["tables"][table]["triggers"])
                )

        for table, required_columns in required_master_columns(args.profile).items():
            columns = inspect_table(cursor, table)
            actual = {column["name"].lower() for column in columns}
            missing = [column for column in required_columns if column.lower() not in actual]
            report["master_tables"][table] = {
                "exists": bool(columns),
                "missing_columns": missing,
                "columns": columns,
            }
            if missing:
                report["blocking_issues"].append(
                    f"Default master lookup dbo.{table} is missing columns: {', '.join(missing)}"
                )

        report["compatible"] = not report["blocking_issues"]
        report["module_scan_included"] = args.include_module_scan
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
