import unittest

import db
from mapping_service import MappingConfig
from purchase_service import insert_purchase, preview_purchase
from validation import ValidationError, validate_invoice_json


INVOICE = {
    "supplier": "AMBEST MARKETING PVT.LTD",
    "invoice_no": "INV123",
    "date": "2026-06-05",
    "items": [
        {
            "item_name": "100PIPERS 750 ML",
            "item_code": "100001",
            "batch": "B001",
            "quantity": 10,
            "rate": 1920,
        },
        {
            "item_name": "SECOND ITEM",
            "batch": "B002",
            "quantity": 2,
            "rate": 100,
        },
    ],
    "tax": {"code": "VAT", "rate": 20, "amount": 500},
    "total": 19700,
}

MANDAI_INVOICE = {
    "supplier": "MOUNT EVEREST",
    "invoice_no": "tFLDR/2026-2027/00042050-01",
    "date": "2026-04-22",
    "items": [
        {
            "item_name": "STOK STRONG BEER",
            "item_code": "S00120",
            "batch": "BR/2022-2023/2916",
            "ml": 500,
            "quantity": 100,
            "rate": 1112,
            "amount": 111200,
            "duty": 144560,
            "vat": 39600,
        }
    ],
    "tax": {"code": "VAT", "rate": 35.61, "amount": 39600},
    "total": 150800,
    "erp_source": {
        "docno": "00042050",
        "income_tax": "3016.00",
        "duty": "144560.00",
    },
}


class FakeCursor:
    def __init__(self, fail_details=False, missing_item_code=None):
        self.fail_details = fail_details
        self.missing_item_code = missing_item_code
        self.executed = []
        self.detail_rows = []
        self.result = None
        self.closed = False

    def execute(self, sql, *params):
        self.executed.append((sql, params))
        normalized = " ".join(sql.upper().split())
        if "SELECT COUNT(1) FROM DBO.MASTERACCOUNTSLEDGER" in normalized:
            self.result = [(1,)]
        elif "SELECT COUNT(1) FROM DBO.STORAGE" in normalized:
            self.result = [(1,)]
        elif "MASTERACCOUNTSLEDGER" in normalized:
            self.result = [("SUP001",)]
        elif "SELECT PACKING, MRP, T3_AMT, T4_AMT" in normalized:
            self.result = [(24, 180, 88.96, 0)]
        elif "FROM DBO.ITEMMST" in normalized and "WHERE ITEMNAME" in normalized:
            self.result = [("ITEM002",)]
        elif "FROM DBO.ITEMMST" in normalized and "WHERE ITEMCODE" in normalized:
            self.result = [] if params[0] == self.missing_item_code else [(params[0],)]
        elif "SP_GETAPPLOCK" in normalized:
            self.result = [(0,)]
        elif "SELECT COUNT(1)" in normalized:
            self.result = [(0,)]
        elif "MAX(TRNID)" in normalized:
            self.result = [(1001,)]
        elif "MAX(TRNNO)" in normalized:
            self.result = [(77,)]
        return self

    def executemany(self, sql, rows):
        if self.fail_details:
            raise RuntimeError("detail failure")
        self.executed.append((sql, tuple(rows)))
        self.detail_rows = list(rows)
        return self

    def fetchmany(self, size):
        return self.result[:size]

    def fetchone(self):
        return self.result[0]

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, fail_details=False, missing_item_code=None):
        self.autocommit = False
        self.cursor_value = FakeCursor(fail_details, missing_item_code)
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class ValidationTests(unittest.TestCase):
    def test_strict_total_rejects_mismatch(self):
        invoice = dict(INVOICE, total=20000)
        with self.assertRaises(ValidationError):
            validate_invoice_json(invoice, strict_total=True)

    def test_non_strict_total_returns_warning(self):
        invoice = dict(INVOICE, total=20000)
        validated = validate_invoice_json(invoice)
        self.assertEqual(len(validated.warnings), 1)


class PurchaseServiceTests(unittest.TestCase):
    def test_preview_rejects_company_or_year_too_long(self):
        connection = FakeConnection()
        with self.assertRaises(db.DatabaseError):
            preview_purchase(
                INVOICE,
                connection,
                companycode="C01",
                yearcode="2026-27",
            )
        self.assertFalse(
            any("INSERT INTO" in sql.upper() for sql, _ in connection.cursor_value.executed)
        )

    def test_preview_resolves_codes_without_inserts(self):
        connection = FakeConnection()
        output = preview_purchase(
            INVOICE,
            connection,
            companycode="C01",
            yearcode="8",
            mapping_config=MappingConfig({}, {}),
        )
        self.assertEqual(output["suppliercode"], "SUP001")
        self.assertEqual(output["items"][0]["itemcode"], "100001")
        self.assertEqual(output["items"][1]["itemcode"], "ITEM002")
        self.assertFalse(
            any("INSERT INTO" in sql.upper() for sql, _ in connection.cursor_value.executed)
        )
        self.assertTrue(connection.rolled_back)

    def test_insert_commits_same_ids_to_details_and_tax(self):
        connection = FakeConnection()
        output = insert_purchase(
            INVOICE,
            connection,
            companycode="C01",
            yearcode="8",
        )
        self.assertTrue(connection.committed)
        self.assertFalse(connection.rolled_back)
        self.assertEqual(output["trnid"], 1001)
        self.assertEqual(output["trnno"], 77)
        self.assertTrue(all(row[2] == 1001 for row in connection.cursor_value.detail_rows))
        self.assertTrue(all(row[3] == 77 for row in connection.cursor_value.detail_rows))
        self.assertEqual(
            [row[4] for row in connection.cursor_value.detail_rows],
            [1, 2],
        )

        inserts = [
            sql for sql, _ in connection.cursor_value.executed if "INSERT INTO" in sql.upper()
        ]
        self.assertEqual(
            set(inserts),
            {
                db.INSERT_TRNIDMST,
                db.INSERT_PURCHASEMAIN,
                db.INSERT_PURCHASEDETAIL,
                db.INSERT_PURCHASETAXDETAIL,
            },
        )
        transaction_master = next(
            params
            for sql, params in connection.cursor_value.executed
            if sql == db.INSERT_TRNIDMST
        )
        self.assertEqual(transaction_master[:5], ("C01", "8", 1001, "Purchase_Add", "A00001"))
        self.assertEqual(transaction_master[6], "N")
        trnid_query = next(
            sql
            for sql, _ in connection.cursor_value.executed
            if "MAX(TRNID)" in sql.upper()
        )
        self.assertIn("FROM dbo.trnidmst", trnid_query)

    def test_failure_rolls_back(self):
        connection = FakeConnection(fail_details=True)
        with self.assertRaises(Exception):
            insert_purchase(
                INVOICE,
                connection,
                companycode="C01",
                yearcode="8",
            )
        self.assertFalse(connection.committed)
        self.assertTrue(connection.rolled_back)

    def test_mandai_profile_matches_manual_purchase_calculation(self):
        connection = FakeConnection()
        output = insert_purchase(
            MANDAI_INVOICE,
            connection,
            companycode="2",
            yearcode="8",
            erp_profile="mandai",
            erp_options={
                "ptype": "PURCHASE",
                "purchaseacccode": "P00002",
                "shopcode": "S00001",
                "checked_by": "A00001",
                "bill_type": "AI",
                "purchase_tax_account": "E00001",
                "rounding_account": "IEX001",
            },
        )
        self.assertTrue(connection.committed)
        self.assertEqual(output["tax_rows_inserted"], 2)
        detail = next(
            rows[0] for sql, rows in connection.cursor_value.executed
            if sql == db.INSERT_MANDAI_PURCHASEDETAIL
        )
        self.assertEqual(detail[7], 100)       # itembox
        self.assertEqual(detail[9], 2400)      # itemquantity
        self.assertEqual(str(detail[6]), "46.33")    # itemrate
        self.assertEqual(str(detail[12]), "1112.00") # itemboxrate
        self.assertEqual(str(detail[21]), "39600.00")
        self.assertEqual(str(detail[22]), "3016.00")
        self.assertEqual(str(detail[23]), "8896.00")
        main = next(
            params for sql, params in connection.cursor_value.executed
            if sql == db.INSERT_MANDAI_PURCHASEMAIN
        )
        self.assertEqual(str(main[13]), "162712.00")
        self.assertEqual(main[9], "00042050")

    def test_supplied_item_code_must_exist_in_item_master(self):
        connection = FakeConnection(missing_item_code="100001")
        with self.assertRaises(Exception):
            preview_purchase(
                INVOICE,
                connection,
                companycode="C01",
                yearcode="8",
            )
        self.assertFalse(
            any("INSERT INTO" in sql.upper() for sql, _ in connection.cursor_value.executed)
        )


if __name__ == "__main__":
    unittest.main()
