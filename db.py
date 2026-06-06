"""Low-level SQL Server operations for purchase insertion.

All INSERT statements in this module target only the ERP purchase transaction:
    trnidmst, purchasemain, purchasedetail, PurchaseTaxDetail
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Optional, Sequence, Tuple

try:
    import pyodbc
except ImportError:
    pyodbc = None


LOGGER = logging.getLogger(__name__)

INSERT_PURCHASEMAIN = """
INSERT INTO dbo.purchasemain
    (companycode, yearcode, trnid, trnno, trndate, suppliercode,
     docno, totamount, totnetamt, narration)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""".strip()

INSERT_TRNIDMST = """
INSERT INTO dbo.trnidmst
    (companycode, yearcode, trnid, trn_type, usercode, TranDate, Sync)
VALUES (?, ?, ?, ?, ?, ?, ?)
""".strip()

INSERT_PURCHASEDETAIL = """
INSERT INTO dbo.purchasedetail
    (companycode, yearcode, trnid, trnno, slno, itemcode, batchno,
     itemrate, itemquantity, itemamount, trndate)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""".strip()

INSERT_PURCHASETAXDETAIL = """
INSERT INTO dbo.PurchaseTaxDetail
    (companycode, yearcode, trnno, TaxCode, TaxRate, TaxAmount)
VALUES (?, ?, ?, ?, ?, ?)
""".strip()


class DatabaseError(RuntimeError):
    """Raised for database-level integration failures."""


def validate_company_year(companycode: str, yearcode: str) -> None:
    """Validate the composite ERP transaction scope."""
    for name, value in (("companycode", companycode), ("yearcode", yearcode)):
        if not isinstance(value, str) or not value.strip():
            raise DatabaseError(f"{name} is required.")
        if len(value) > 6:
            raise DatabaseError(f"{name} exceeds the ERP maximum length of 6.")


def validate_transaction_context(
    companycode: str,
    yearcode: str,
    transaction_type: str,
    usercode: str,
    sync: str,
) -> None:
    """Validate values against the observed dbo.trnidmst varchar sizes."""
    validate_company_year(companycode, yearcode)
    fields = {
        "transaction_type": (transaction_type, 30),
        "usercode": (usercode, 6),
    }
    for name, (value, max_length) in fields.items():
        if not isinstance(value, str) or not value.strip():
            raise DatabaseError(f"{name} is required.")
        if len(value) > max_length:
            raise DatabaseError(f"{name} exceeds the ERP maximum length of {max_length}.")
    if not isinstance(sync, str) or len(sync) != 1:
        raise DatabaseError("sync must be exactly one character.")


@dataclass(frozen=True)
class LookupQueries:
    supplier: str
    item: str


def connect(connection_string: str) -> Any:
    """Create an explicit-transaction pyodbc connection."""
    if pyodbc is None:
        raise DatabaseError("pyodbc is not installed. Run: pip install pyodbc")
    return pyodbc.connect(connection_string, autocommit=False)


def validate_lookup_sql(sql: str, name: str) -> None:
    """Ensure configurable master lookup SQL cannot perform writes."""
    blocked = re.compile(
        r"\b(INSERT|UPDATE|DELETE|MERGE|EXEC|EXECUTE|DROP|ALTER|CREATE|TRUNCATE)\b",
        re.IGNORECASE,
    )
    if not sql.lstrip().upper().startswith("SELECT") or ";" in sql or blocked.search(sql):
        raise DatabaseError(f"{name} lookup SQL must be one SELECT-only statement.")


def lookup_single_value(cursor: Any, sql: str, params: Sequence[Any], name: str) -> str:
    """Execute a SELECT lookup that must return exactly one non-empty value."""
    validate_lookup_sql(sql, name)
    try:
        cursor.execute(sql, *params)
        rows = cursor.fetchmany(2)
    except Exception as exc:
        raise DatabaseError(f"{name} lookup query failed: {exc}") from exc
    if not rows:
        raise DatabaseError(f"{name} lookup returned no match.")
    if len(rows) > 1:
        raise DatabaseError(f"{name} lookup returned multiple matches.")
    value = str(rows[0][0] or "").strip()
    if not value:
        raise DatabaseError(f"{name} lookup returned an empty value.")
    return value


def _application_lock(cursor: Any, resource: str) -> None:
    cursor.execute(
        """
        DECLARE @result int;
        EXEC @result = sys.sp_getapplock
            @Resource = ?,
            @LockMode = 'Exclusive',
            @LockOwner = 'Transaction',
            @LockTimeout = 10000;
        SELECT @result
        """,
        resource,
    )
    result = int(cursor.fetchone()[0])
    if result < 0:
        raise DatabaseError(f"Unable to acquire SQL transaction lock '{resource}'.")


def get_next_ids(cursor: Any, companycode: str, yearcode: str) -> Tuple[int, int]:
    """Generate concurrency-protected IDs from their authoritative ERP tables."""
    _application_lock(cursor, "trnidmst:trnid")
    _application_lock(cursor, f"purchasemain:trnno:{companycode}:{yearcode}")

    cursor.execute(
        """
        SELECT ISNULL(MAX(trnid), 0) + 1
        FROM dbo.trnidmst WITH (UPDLOCK, HOLDLOCK)
        """
    )
    trnid = int(cursor.fetchone()[0])

    cursor.execute(
        """
        SELECT ISNULL(MAX(trnno), 0) + 1
        FROM dbo.purchasemain WITH (UPDLOCK, HOLDLOCK)
        WHERE companycode = ? AND yearcode = ?
        """,
        companycode,
        yearcode,
    )
    trnno = int(cursor.fetchone()[0])
    return trnid, trnno


def insert_transaction_master(
    cursor: Any,
    *,
    companycode: str,
    yearcode: str,
    trnid: int,
    transaction_type: str,
    usercode: str,
    transaction_date: Any,
    sync: str,
) -> None:
    """Create the parent ERP transaction row required by purchasemain."""
    LOGGER.info("Inserting trnidmst row for trnid=%s.", trnid)
    cursor.execute(
        INSERT_TRNIDMST,
        companycode,
        yearcode,
        trnid,
        transaction_type,
        usercode,
        transaction_date,
        sync,
    )


def ensure_invoice_not_duplicate(
    cursor: Any,
    companycode: str,
    yearcode: str,
    suppliercode: str,
    docno: str,
) -> None:
    """Reject a repeated supplier invoice, protecting automated retry flows."""
    cursor.execute(
        """
        SELECT COUNT(1)
        FROM dbo.purchasemain WITH (UPDLOCK, HOLDLOCK)
        WHERE companycode = ?
          AND yearcode = ?
          AND suppliercode = ?
          AND docno = ?
        """,
        companycode,
        yearcode,
        suppliercode,
        docno,
    )
    if int(cursor.fetchone()[0]) > 0:
        raise DatabaseError(
            f"Purchase invoice '{docno}' already exists for supplier '{suppliercode}'."
        )


def find_existing_purchase(
    cursor: Any,
    companycode: str,
    yearcode: str,
    suppliercode: str,
    docno: str,
) -> Optional[dict]:
    """Find an existing ERP purchase during preview without taking write locks."""
    cursor.execute(
        """
        SELECT TOP 1 trnid, trnno, trndate
        FROM dbo.purchasemain
        WHERE companycode = ?
          AND yearcode = ?
          AND suppliercode = ?
          AND docno = ?
        ORDER BY trnid DESC
        """,
        companycode,
        yearcode,
        suppliercode,
        docno,
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {"trnid": int(row[0]), "trnno": int(row[1]), "trndate": row[2]}


def insert_purchase_main(cursor: Any, values: Sequence[Any]) -> None:
    LOGGER.info("Inserting purchasemain row.")
    cursor.execute(INSERT_PURCHASEMAIN, *values)


def insert_purchase_details(cursor: Any, rows: Sequence[Sequence[Any]]) -> None:
    LOGGER.info("Inserting %s purchasedetail rows.", len(rows))
    cursor.executemany(INSERT_PURCHASEDETAIL, rows)


def insert_purchase_tax(cursor: Any, values: Optional[Sequence[Any]]) -> int:
    if values is None:
        return 0
    LOGGER.info("Inserting PurchaseTaxDetail row.")
    cursor.execute(INSERT_PURCHASETAXDETAIL, *values)
    return 1
