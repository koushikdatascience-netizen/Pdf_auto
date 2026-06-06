"""Read-only audit of ERP item-master creation rules and dependencies."""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import db
from schema_check import (
    inspect_checks,
    inspect_foreign_keys,
    inspect_inbound_foreign_keys,
    inspect_indexes,
    inspect_table,
    inspect_triggers,
)


WRITE_PATTERNS = {
    "inserts_itemmst": re.compile(r"\bINSERT\s+(?:INTO\s+)?(?:\[?dbo\]?\.)?\[?itemmst\]?\b", re.I),
    "updates_itemmst": re.compile(r"\bUPDATE\s+(?:\[?dbo\]?\.)?\[?itemmst\]?\b", re.I),
    "deletes_itemmst": re.compile(r"\bDELETE\s+(?:FROM\s+)?(?:\[?dbo\]?\.)?\[?itemmst\]?\b", re.I),
    "merges_itemmst": re.compile(r"\bMERGE\s+(?:INTO\s+)?(?:\[?dbo\]?\.)?\[?itemmst\]?\b", re.I),
}


def rows(cursor: Any, sql: str, *params: Any) -> list[dict[str, Any]]:
    cursor.execute(sql, *params)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit ERP item creation behavior without writes.")
    parser.add_argument("--output", type=Path, default=Path("item_creation_audit.json"))
    args = parser.parse_args()
    connection_string = os.environ.get("SQLSERVER_CONNECTION_STRING")
    if not connection_string:
        raise SystemExit("Set SQLSERVER_CONNECTION_STRING first.")

    connection = db.connect(connection_string)
    cursor = connection.cursor()
    try:
        identity = rows(
            cursor,
            "SELECT DB_NAME() AS database_name, @@SERVERNAME AS server_name",
        )[0]
        modules = rows(
            cursor,
            """
            SELECT
                OBJECT_SCHEMA_NAME(m.object_id) AS schema_name,
                OBJECT_NAME(m.object_id) AS object_name,
                o.type_desc,
                m.definition
            FROM sys.sql_modules m
            JOIN sys.objects o ON o.object_id = m.object_id
            WHERE m.definition LIKE '%itemmst%'
               OR OBJECT_NAME(m.object_id) LIKE '%item%'
               OR OBJECT_NAME(m.object_id) LIKE '%master%'
            ORDER BY o.type_desc, OBJECT_NAME(m.object_id)
            """,
        )
        classified_modules = []
        for module in modules:
            definition = module.get("definition") or ""
            actions = [name for name, pattern in WRITE_PATTERNS.items() if pattern.search(definition)]
            module["actions"] = actions
            module["references_itemmst"] = "itemmst" in definition.lower()
            classified_modules.append(module)

        trigger_definitions = rows(
            cursor,
            """
            SELECT
                tr.name AS trigger_name,
                tr.is_disabled,
                tr.is_instead_of_trigger,
                m.definition
            FROM sys.triggers tr
            LEFT JOIN sys.sql_modules m ON m.object_id = tr.object_id
            WHERE tr.parent_id = OBJECT_ID('dbo.itemmst')
            ORDER BY tr.name
            """,
        )
        related_tables = rows(
            cursor,
            """
            SELECT TABLE_SCHEMA AS schema_name, TABLE_NAME AS table_name
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_TYPE = 'BASE TABLE'
              AND (
                    TABLE_NAME LIKE '%item%'
                 OR TABLE_NAME LIKE '%brand%'
                 OR TABLE_NAME LIKE '%category%'
                 OR TABLE_NAME LIKE '%packing%'
                 OR TABLE_NAME LIKE '%strength%'
                 OR TABLE_NAME LIKE '%manufacturer%'
              )
            ORDER BY TABLE_NAME
            """,
        )
        item_code_modules = [
            module
            for module in classified_modules
            if module["references_itemmst"]
            and re.search(r"MAX\s*\(|itemcode|shortcode|barcode|IDENT_CURRENT|NEXT\s+VALUE", module["definition"], re.I)
        ]
        report = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "read_only_audit": True,
            "database": identity,
            "itemmst": {
                "columns": inspect_table(cursor, "itemmst"),
                "indexes": inspect_indexes(cursor, "itemmst"),
                "foreign_keys": inspect_foreign_keys(cursor, "itemmst"),
                "inbound_foreign_keys": inspect_inbound_foreign_keys(cursor, "itemmst"),
                "checks": inspect_checks(cursor, "itemmst"),
                "triggers": inspect_triggers(cursor, "itemmst"),
                "trigger_definitions": trigger_definitions,
            },
            "write_modules": [module for module in classified_modules if module["actions"]],
            "item_code_candidate_modules": item_code_modules,
            "all_related_modules": classified_modules,
            "related_tables": related_tables,
            "next_steps": [
                "Review write_modules and identify the procedure used by the ERP Item Master save button.",
                "Compare all tables written by that procedure before implementing API item creation.",
                "Confirm item-code, barcode, tax, brand, manufacturer, category, packing, and permission rules.",
                "Use the ERP procedure when available; do not directly insert only into itemmst.",
            ],
        }
        args.output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"Created read-only item creation audit: {args.output}")
        print(f"Item-writing modules found: {len(report['write_modules'])}")
        for module in report["write_modules"]:
            print(
                f"- {module['schema_name']}.{module['object_name']} "
                f"({', '.join(module['actions'])})"
            )
    finally:
        cursor.close()
        connection.rollback()
        connection.close()


if __name__ == "__main__":
    main()
