import unittest
from decimal import Decimal

from purchase_db import (
    INSERT_PURCHASEDETAIL_SQL,
    INSERT_PURCHASEMAIN_SQL,
    INSERT_PURCHASETAXDETAIL_SQL,
    PurchaseDatabaseError,
    PurchaseValidationError,
    insert_purchase,
    validate_json,
)


VALID_INVOICE = {
    "supplier": "ABC Traders",
    "invoice_no": "INV-101",
    "date": "2026-06-05",
    "items": [
        {
            "name": "ITEM001",
            "qty": 10,
            "price": 100,
            "batch": "B001",
        }
    ],
    "tax": {
        "gst_rate": 18,
        "gst_amount": 180,
    },
    "total": 1180,
}


class ValidateJsonTests(unittest.TestCase):
    def test_valid_invoice(self):
        purchase = validate_json(VALID_INVOICE, companycode="C01", yearcode="2026-27")
        self.assertEqual(purchase.companycode, "C01")
        self.assertEqual(purchase.total, Decimal("1180.00"))
        self.assertEqual(len(purchase.items), 1)
        self.assertEqual(purchase.items[0].itemamount, Decimal("1000.00"))

    def test_empty_items_rejected(self):
        invoice = dict(VALID_INVOICE, items=[])
        with self.assertRaises(PurchaseValidationError):
            validate_json(invoice, companycode="C01", yearcode="2026-27")

    def test_missing_batch_rejected(self):
        invoice = dict(VALID_INVOICE, items=[{"name": "ITEM001", "qty": 1, "price": 100}])
        with self.assertRaises(PurchaseValidationError):
            validate_json(invoice, companycode="C01", yearcode="2026-27")

    def test_total_mismatch_rejected(self):
        invoice = dict(VALID_INVOICE, total=999)
        with self.assertRaises(PurchaseValidationError):
            validate_json(invoice, companycode="C01", yearcode="2026-27")

    def test_multiple_items(self):
        invoice = dict(
            VALID_INVOICE,
            items=[
                {"name": "ITEM001", "qty": 2, "price": 100, "batch": "B001"},
                {"name": "ITEM002", "qty": 4, "price": 50, "batch": "B002"},
            ],
            tax={"gst_rate": 18, "gst_amount": 72},
            total=472,
        )
        purchase = validate_json(invoice, companycode="C01", yearcode="2026-27")
        self.assertEqual(len(purchase.items), 2)
        self.assertEqual(sum(item.itemamount for item in purchase.items), Decimal("400.00"))


class FakeCursor:
    def __init__(self, fail_on_details=False):
        self.fail_on_details = fail_on_details
        self.executed = []
        self.detail_rows = []
        self.next_fetchone = None
        self.closed = False

    def execute(self, sql, *params):
        self.executed.append((sql, params))
        normalized = " ".join(sql.upper().split())
        if "SUPPLIERMASTER" in normalized:
            self.next_fetchmany = [("SUP001",)]
        elif "SP_GETAPPLOCK" in normalized:
            self.next_fetchone = (0,)
        elif "MAX(TRNID)" in normalized:
            self.next_fetchone = (101,)
        elif "MAX(TRNNO)" in normalized:
            self.next_fetchone = (7,)
        return self

    def executemany(self, sql, rows):
        if self.fail_on_details:
            raise RuntimeError("simulated detail insert failure")
        self.executed.append((sql, tuple(rows)))
        self.detail_rows = list(rows)
        return self

    def fetchone(self):
        return self.next_fetchone

    def fetchmany(self, size):
        return self.next_fetchmany[:size]

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, fail_on_details=False):
        self.autocommit = False
        self.cursor_instance = FakeCursor(fail_on_details=fail_on_details)
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class InsertPurchaseTests(unittest.TestCase):
    def test_success_commits_and_uses_same_ids(self):
        connection = FakeConnection()
        result = insert_purchase(
            VALID_INVOICE,
            connection,
            companycode="C01",
            yearcode="2026-27",
        )

        self.assertTrue(connection.committed)
        self.assertFalse(connection.rolled_back)
        self.assertEqual(result["trnid"], 101)
        self.assertEqual(result["trnno"], 7)
        self.assertEqual(connection.cursor_instance.detail_rows[0][2], 101)
        self.assertEqual(connection.cursor_instance.detail_rows[0][6], 7)

        insert_sql = [
            sql for sql, _ in connection.cursor_instance.executed if "INSERT INTO" in sql.upper()
        ]
        self.assertEqual(
            set(insert_sql),
            {
                INSERT_PURCHASEMAIN_SQL,
                INSERT_PURCHASEDETAIL_SQL,
                INSERT_PURCHASETAXDETAIL_SQL,
            },
        )

    def test_database_failure_rolls_back(self):
        connection = FakeConnection(fail_on_details=True)
        with self.assertRaises(PurchaseDatabaseError):
            insert_purchase(
                VALID_INVOICE,
                connection,
                companycode="C01",
                yearcode="2026-27",
            )
        self.assertFalse(connection.committed)
        self.assertTrue(connection.rolled_back)


if __name__ == "__main__":
    unittest.main()
