"""Insert validated invoice JSON into Microsoft SQL Server purchase tables.

Only these tables are written by this module:
    - purchasemain
    - purchasedetail
    - PurchaseTaxDetail

The caller supplies an open pyodbc connection. Supplier lookup is read-only and
configurable because the supplier master table was not specified.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

try:
    import pyodbc
except ImportError:  # Allows validation/unit tests before pyodbc is installed.
    pyodbc = None


LOGGER = logging.getLogger(__name__)
MONEY_PLACES = Decimal("0.01")

# Change this to match the ERP supplier master schema. It must return exactly
# one suppliercode column and should remain a SELECT-only query.
DEFAULT_SUPPLIER_LOOKUP_SQL = """
SELECT suppliercode
FROM dbo.suppliermaster
WHERE suppliername = ? AND companycode = ?
""".strip()

INSERT_PURCHASEMAIN_SQL = """
INSERT INTO dbo.purchasemain
    (companycode, yearcode, trnid, trnno, trndate,
     suppliercode, docno, totamount, totnetamt)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
""".strip()

# trnid is included because the integration rules require the same trnid in
# purchasemain and purchasedetail.
INSERT_PURCHASEDETAIL_SQL = """
INSERT INTO dbo.purchasedetail
    (companycode, yearcode, trnid, itemcode, batchno, itemrate,
     trnno, trndate, itemquantity, itemamount)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""".strip()

INSERT_PURCHASETAXDETAIL_SQL = """
INSERT INTO dbo.PurchaseTaxDetail
    (trnno, companycode, yearcode, TaxCode, TaxRate, TaxAmount)
VALUES (?, ?, ?, ?, ?, ?)
""".strip()


class PurchaseError(Exception):
    """Base exception for purchase integration failures."""


class PurchaseValidationError(PurchaseError):
    """Input JSON is missing required data or contains invalid values."""


class SupplierNotFoundError(PurchaseError):
    """Supplier lookup returned no matching supplier."""


class SupplierLookupError(PurchaseError):
    """Supplier lookup returned an ambiguous or invalid result."""


class PurchaseDatabaseError(PurchaseError):
    """Database operation failed and the transaction was rolled back."""


@dataclass(frozen=True)
class PurchaseItem:
    itemcode: str
    batchno: str
    itemquantity: int
    itemrate: Decimal
    itemamount: Decimal


@dataclass(frozen=True)
class PurchaseTax:
    tax_code: Optional[str]
    tax_rate: Optional[Decimal]
    tax_amount: Optional[Decimal]


@dataclass(frozen=True)
class ValidatedPurchase:
    companycode: str
    yearcode: str
    supplier_name: str
    docno: str
    trndate: datetime
    total: Decimal
    items: Sequence[PurchaseItem]
    tax: Optional[PurchaseTax]


def _required_text(value: Any, field_name: str, max_length: Optional[int] = None) -> str:
    if value is None:
        raise PurchaseValidationError(f"Required field '{field_name}' is missing.")
    text = str(value).strip()
    if not text:
        raise PurchaseValidationError(f"Required field '{field_name}' cannot be empty.")
    if max_length is not None and len(text) > max_length:
        raise PurchaseValidationError(
            f"Field '{field_name}' exceeds the maximum length of {max_length}."
        )
    return text


def _optional_text(value: Any, field_name: str, max_length: Optional[int] = None) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if max_length is not None and len(text) > max_length:
        raise PurchaseValidationError(
            f"Field '{field_name}' exceeds the maximum length of {max_length}."
        )
    return text


def _decimal_value(
    value: Any,
    field_name: str,
    *,
    required: bool = True,
    non_negative: bool = True,
) -> Optional[Decimal]:
    if value is None:
        if required:
            raise PurchaseValidationError(f"Required field '{field_name}' is missing.")
        return None
    if isinstance(value, bool):
        raise PurchaseValidationError(f"Field '{field_name}' must be numeric.")
    try:
        number = Decimal(str(value)).quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise PurchaseValidationError(f"Field '{field_name}' must be numeric.") from None
    if not number.is_finite():
        raise PurchaseValidationError(f"Field '{field_name}' must be finite.")
    if non_negative and number < 0:
        raise PurchaseValidationError(f"Field '{field_name}' cannot be negative.")
    return number


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise PurchaseValidationError(f"Field '{field_name}' must be a positive integer.")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise PurchaseValidationError(f"Field '{field_name}' must be a positive integer.") from None
    if number <= 0 or str(value).strip() not in {str(number), f"{number}.0"}:
        raise PurchaseValidationError(f"Field '{field_name}' must be a positive integer.")
    return number


def _parse_date(value: Any, field_name: str = "date") -> datetime:
    text = _required_text(value, field_name)
    supported_formats = ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y")
    for date_format in supported_formats:
        try:
            return datetime.strptime(text, date_format)
        except ValueError:
            pass
    raise PurchaseValidationError(
        f"Field '{field_name}' must use YYYY-MM-DD, YYYY-MM-DDTHH:MM:SS, or DD/MM/YYYY."
    )


def validate_json(
    json_data: Mapping[str, Any],
    *,
    companycode: Optional[str] = None,
    yearcode: Optional[str] = None,
    validate_total: bool = True,
) -> ValidatedPurchase:
    """Validate and normalize invoice JSON without accessing the database."""
    if not isinstance(json_data, Mapping):
        raise PurchaseValidationError("Invoice JSON must be an object.")

    resolved_companycode = _required_text(
        companycode if companycode is not None else json_data.get("companycode"),
        "companycode",
    )
    resolved_yearcode = _required_text(
        yearcode if yearcode is not None else json_data.get("yearcode"),
        "yearcode",
    )
    supplier_name = _required_text(json_data.get("supplier"), "supplier")
    docno = _required_text(json_data.get("invoice_no"), "invoice_no")
    trndate = _parse_date(json_data.get("date"))
    total = _decimal_value(json_data.get("total"), "total")

    raw_items = json_data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise PurchaseValidationError("Field 'items' must contain at least one item.")

    items: List[PurchaseItem] = []
    for index, raw_item in enumerate(raw_items):
        prefix = f"items[{index}]"
        if not isinstance(raw_item, Mapping):
            raise PurchaseValidationError(f"Field '{prefix}' must be an object.")
        itemcode = _required_text(raw_item.get("name"), f"{prefix}.name")
        batchno = _required_text(raw_item.get("batch"), f"{prefix}.batch")
        quantity = _positive_int(raw_item.get("qty"), f"{prefix}.qty")
        rate = _decimal_value(raw_item.get("price"), f"{prefix}.price")
        calculated_amount = (rate * quantity).quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)
        supplied_amount = _decimal_value(
            raw_item.get("amount"),
            f"{prefix}.amount",
            required=False,
        )
        if supplied_amount is not None and supplied_amount != calculated_amount:
            raise PurchaseValidationError(
                f"Field '{prefix}.amount' does not equal qty * price "
                f"({calculated_amount})."
            )
        items.append(
            PurchaseItem(
                itemcode=itemcode,
                batchno=batchno,
                itemquantity=quantity,
                itemrate=rate,
                itemamount=calculated_amount,
            )
        )

    purchase_tax: Optional[PurchaseTax] = None
    raw_tax = json_data.get("tax")
    if raw_tax is not None:
        if not isinstance(raw_tax, Mapping):
            raise PurchaseValidationError("Field 'tax' must be an object when provided.")
        tax_rate = _decimal_value(raw_tax.get("gst_rate"), "tax.gst_rate", required=False)
        tax_amount = _decimal_value(raw_tax.get("gst_amount"), "tax.gst_amount", required=False)
        tax_code = _optional_text(raw_tax.get("tax_code", "GST"), "tax.tax_code")
        purchase_tax = PurchaseTax(tax_code=tax_code, tax_rate=tax_rate, tax_amount=tax_amount)

    if validate_total:
        items_total = sum((item.itemamount for item in items), Decimal("0.00"))
        tax_total = (
            purchase_tax.tax_amount
            if purchase_tax is not None and purchase_tax.tax_amount is not None
            else Decimal("0.00")
        )
        expected_total = (items_total + tax_total).quantize(MONEY_PLACES)
        if total != expected_total:
            raise PurchaseValidationError(
                f"Invoice total {total} does not equal items plus tax {expected_total}."
            )

    return ValidatedPurchase(
        companycode=resolved_companycode,
        yearcode=resolved_yearcode,
        supplier_name=supplier_name,
        docno=docno,
        trndate=trndate,
        total=total,
        items=tuple(items),
        tax=purchase_tax,
    )


def get_supplier_code(
    cursor: Any,
    supplier_name: str,
    companycode: str,
    *,
    supplier_lookup_sql: str = DEFAULT_SUPPLIER_LOOKUP_SQL,
) -> str:
    """Resolve supplier name to suppliercode using a read-only lookup query."""
    normalized_sql = supplier_lookup_sql.lstrip().upper()
    write_keywords = re.compile(
        r"\b(INSERT|UPDATE|DELETE|MERGE|EXEC|EXECUTE|DROP|ALTER|CREATE|TRUNCATE)\b",
        re.IGNORECASE,
    )
    if (
        not normalized_sql.startswith("SELECT")
        or ";" in supplier_lookup_sql
        or write_keywords.search(supplier_lookup_sql)
    ):
        raise SupplierLookupError("Supplier lookup SQL must be a SELECT statement.")

    LOGGER.debug("Resolving supplier '%s' for company '%s'.", supplier_name, companycode)
    cursor.execute(supplier_lookup_sql, supplier_name, companycode)
    rows = cursor.fetchmany(2)
    if not rows:
        raise SupplierNotFoundError(
            f"Supplier '{supplier_name}' was not found for company '{companycode}'."
        )
    if len(rows) > 1:
        raise SupplierLookupError(
            f"Supplier '{supplier_name}' is ambiguous for company '{companycode}'."
        )
    suppliercode = _required_text(rows[0][0], "suppliercode")
    return suppliercode


def _acquire_transaction_lock(cursor: Any, resource: str) -> None:
    """Acquire a SQL Server application lock owned by the current transaction."""
    cursor.execute(
        """
        DECLARE @lock_result int;
        EXEC @lock_result = sys.sp_getapplock
            @Resource = ?,
            @LockMode = 'Exclusive',
            @LockOwner = 'Transaction',
            @LockTimeout = 10000;
        SELECT @lock_result;
        """,
        resource,
    )
    result = int(cursor.fetchone()[0])
    if result < 0:
        raise PurchaseDatabaseError(
            f"Could not acquire ID allocation lock '{resource}' (result {result})."
        )


def get_next_ids(cursor: Any, companycode: str, yearcode: str) -> Tuple[int, int]:
    """Allocate trnid and trnno under locks held until transaction completion.

    This implements the required MAX(...)+1 rules while preventing concurrent
    sessions using this function from allocating the same IDs.
    """
    LOGGER.debug("Allocating trnid and trnno for %s/%s.", companycode, yearcode)
    _acquire_transaction_lock(cursor, "purchasemain:trnid")
    _acquire_transaction_lock(cursor, f"purchasemain:trnno:{companycode}:{yearcode}")

    cursor.execute(
        """
        SELECT ISNULL(MAX(trnid), 0) + 1
        FROM dbo.purchasemain WITH (UPDLOCK, HOLDLOCK)
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


def insert_purchase(
    json_data: Mapping[str, Any],
    connection: Any,
    *,
    companycode: Optional[str] = None,
    yearcode: Optional[str] = None,
    supplier_lookup_sql: str = DEFAULT_SUPPLIER_LOOKUP_SQL,
    validate_total: bool = True,
) -> Dict[str, Any]:
    """Validate and atomically insert a purchase using an open pyodbc connection."""
    results = insert_purchase_batch(
        [json_data],
        connection,
        companycode=companycode,
        yearcode=yearcode,
        supplier_lookup_sql=supplier_lookup_sql,
        validate_total=validate_total,
    )
    return results[0]


def insert_purchase_batch(
    purchases_json: Sequence[Mapping[str, Any]],
    connection: Any,
    *,
    companycode: Optional[str] = None,
    yearcode: Optional[str] = None,
    supplier_lookup_sql: str = DEFAULT_SUPPLIER_LOOKUP_SQL,
    validate_total: bool = True,
) -> List[Dict[str, Any]]:
    """Insert multiple purchases in one all-or-nothing database transaction."""
    if not purchases_json:
        raise PurchaseValidationError("At least one purchase is required.")

    purchases = [
        validate_json(
            purchase_json,
            companycode=companycode,
            yearcode=yearcode,
            validate_total=validate_total,
        )
        for purchase_json in purchases_json
    ]

    if connection is None:
        raise PurchaseDatabaseError("A valid pyodbc connection is required.")

    original_autocommit = bool(getattr(connection, "autocommit", False))
    cursor = None
    results: List[Dict[str, Any]] = []
    try:
        if original_autocommit:
            connection.autocommit = False

        cursor = connection.cursor()
        cursor.execute("SET XACT_ABORT ON")
        cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")

        for purchase in purchases:
            suppliercode = get_supplier_code(
                cursor,
                purchase.supplier_name,
                purchase.companycode,
                supplier_lookup_sql=supplier_lookup_sql,
            )
            trnid, trnno = get_next_ids(cursor, purchase.companycode, purchase.yearcode)

            LOGGER.info(
                "Inserting purchase docno=%s trnid=%s trnno=%s with %s items.",
                purchase.docno,
                trnid,
                trnno,
                len(purchase.items),
            )

            cursor.execute(
                INSERT_PURCHASEMAIN_SQL,
                purchase.companycode,
                purchase.yearcode,
                trnid,
                trnno,
                purchase.trndate,
                suppliercode,
                purchase.docno,
                purchase.total,
                purchase.total,
            )

            detail_rows = [
                (
                    purchase.companycode,
                    purchase.yearcode,
                    trnid,
                    item.itemcode,
                    item.batchno,
                    item.itemrate,
                    trnno,
                    purchase.trndate,
                    item.itemquantity,
                    item.itemamount,
                )
                for item in purchase.items
            ]
            cursor.executemany(INSERT_PURCHASEDETAIL_SQL, detail_rows)

            tax_rows_inserted = 0
            if purchase.tax is not None:
                cursor.execute(
                    INSERT_PURCHASETAXDETAIL_SQL,
                    trnno,
                    purchase.companycode,
                    purchase.yearcode,
                    purchase.tax.tax_code,
                    purchase.tax.tax_rate,
                    purchase.tax.tax_amount,
                )
                tax_rows_inserted = 1

            results.append(
                {
                    "trnid": trnid,
                    "trnno": trnno,
                    "suppliercode": suppliercode,
                    "detail_rows_inserted": len(detail_rows),
                    "tax_rows_inserted": tax_rows_inserted,
                    "total": str(purchase.total),
                }
            )

        connection.commit()
        LOGGER.info("Purchase batch committed successfully: %s purchases.", len(results))
        return results
    except PurchaseError:
        connection.rollback()
        LOGGER.exception("Purchase insertion rejected; transaction rolled back.")
        raise
    except Exception as exc:
        connection.rollback()
        LOGGER.exception("Purchase insertion failed; transaction rolled back.")
        raise PurchaseDatabaseError(f"Purchase insertion failed: {exc}") from exc
    finally:
        if cursor is not None:
            cursor.close()
        if original_autocommit:
            connection.autocommit = True


def create_connection(connection_string: str) -> Any:
    """Create a pyodbc SQL Server connection with explicit transactions."""
    if pyodbc is None:
        raise RuntimeError("pyodbc is not installed. Install it with: pip install pyodbc")
    return pyodbc.connect(connection_string, autocommit=False)


def main() -> None:
    """CLI example: python purchase_db.py invoice.json --company C01 --year 2026-27"""
    parser = argparse.ArgumentParser(description="Insert validated invoice JSON into SQL Server.")
    parser.add_argument("json_file", type=Path)
    parser.add_argument("--company", required=True, help="ERP companycode")
    parser.add_argument("--year", required=True, help="ERP yearcode")
    parser.add_argument(
        "--skip-total-validation",
        action="store_true",
        help="Allow totals containing charges/discounts not represented in the JSON.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    connection_string = os.environ.get("SQLSERVER_CONNECTION_STRING")
    if not connection_string:
        raise SystemExit("Set the SQLSERVER_CONNECTION_STRING environment variable.")

    invoice = json.loads(args.json_file.read_text(encoding="utf-8"))
    connection = create_connection(connection_string)
    try:
        result = insert_purchase(
            invoice,
            connection,
            companycode=args.company,
            yearcode=args.year,
            validate_total=not args.skip_total_validation,
        )
        print(json.dumps(result, indent=2))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
