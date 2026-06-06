import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from integration_api.config import Settings
from integration_api.main import create_app
from mapping_service import MappingConfig


INVOICE = {
    "supplier": "BEVCO (FL)",
    "invoice_no": "API-TEST-001",
    "date": "2026-06-05",
    "items": [
        {
            "item_name": "BAGPIPER 375 ML",
            "item_code": "B00025",
            "batch": "TEST-B001",
            "ml": 375,
            "packing": "24",
            "strength_name": "25 UP",
            "quantity": 2,
            "rate": 100,
        }
    ],
    "tax": {"code": "VAT", "rate": 20, "amount": 40},
    "total": 240,
}

EXTRACTED = {
    "page_count": 1,
    "extraction_method": "native_pdf_text_pymupdf",
    "needs_ocr": False,
    "header": {"demand_id": "PDF-TEST-001", "date": "05/06/2026"},
    "items": [
        {
            "sl": 1,
            "label_name": "BAGPIPER 375 ML",
            "batch": "TEST-B001",
            "capacity_ml": 375,
            "quantity_cases": 2,
            "mfg_amount": 200,
            "manufacturer": "BEVCO (FL)",
        }
    ],
    "manufacturer_groups": [
        {
            "manufacturer": "BEVCO (FL)",
            "item_sl_numbers": [1],
            "total_mfg_amount": 200,
            "total_vat": 40,
        }
    ],
}


class FakeCursor:
    def __init__(self):
        self.result = []
        self.closed = False
        self.detail_rows = []

    def execute(self, sql, *params):
        normalized = " ".join(sql.upper().split())
        if "SELECT TOP 25 LEDGERCODE" in normalized:
            self.result = [("B00011", "BEVCO (FL)")]
        elif "SELECT TOP 25 ITEMCODE" in normalized or "SELECT TOP 10 ITEMCODE" in normalized:
            self.result = [("B00025", "BAGPIPER 375 ML", 375, "24", "25 UP")]
        elif "MASTERACCOUNTSLEDGER" in normalized:
            self.result = [("B00011",)]
        elif "FROM DBO.ITEMMST" in normalized:
            self.result = [(params[0],)]
        elif "SELECT COUNT(1)" in normalized:
            self.result = [(0,)]
        elif "SP_GETAPPLOCK" in normalized:
            self.result = [(0,)]
        elif "MAX(TRNID)" in normalized:
            self.result = [(501,)]
        elif "MAX(TRNNO)" in normalized:
            self.result = [(91,)]
        return self

    def executemany(self, sql, rows):
        self.detail_rows = list(rows)
        return self

    def fetchmany(self, size):
        return self.result[:size]

    def fetchone(self):
        return self.result[0]

    def fetchall(self):
        return self.result

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self):
        self.autocommit = False
        self.cursor_value = FakeCursor()
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


class IntegrationApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = Settings(
            connection_string="fake",
            api_key="test-api-key",
            approval_secret="test-approval-secret",
            host="127.0.0.1",
            port=47831,
            max_pdf_bytes=1024 * 1024,
            approval_ttl_seconds=900,
            state_db_path=root / "state.sqlite3",
            audit_log_path=root / "audit.log",
            upload_dir=root / "uploads",
            mapping_store_path=root / "mappings.json",
            supplier_lookup_sql=(
                "SELECT ledgerCode FROM dbo.MasterAccountsLedger "
                "WHERE ledgerName=? AND companyCode=?"
            ),
            item_lookup_sql="SELECT itemcode FROM dbo.itemmst WHERE itemname=?",
            item_code_verify_sql="SELECT itemcode FROM dbo.itemmst WHERE itemcode=?",
            item_stock_lookup_sql="",
            transaction_type="Purchase_Add",
            usercode="A00001",
            sync="N",
            mapping_config=MappingConfig({}, {}),
            enable_docs=True,
        )
        self.connections = []

        def fake_connect(_):
            connection = FakeConnection()
            self.connections.append(connection)
            return connection

        self.patch = patch("integration_api.main.db.connect", side_effect=fake_connect)
        self.patch.start()
        self.client = TestClient(create_app(self.settings))
        self.headers = {"X-API-Key": "test-api-key"}

    def tearDown(self):
        self.client.close()
        handler = self.client.app.state.audit_handler
        if handler in __import__("logging").getLogger().handlers:
            __import__("logging").getLogger().removeHandler(handler)
            handler.close()
        self.patch.stop()
        self.temp.cleanup()

    def test_authentication_required(self):
        response = self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 401)

    def test_supplier_mapping_is_verified_saved_and_available_without_restart(self):
        saved = self.client.post(
            "/api/v1/mappings/suppliers",
            headers=self.headers,
            json={
                "companycode": "2",
                "source_name": "PDF SUPPLIER NAME",
                "target_name": "BEVCO (FL)",
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["suppliercode"], "B00011")

        mappings = self.client.get("/api/v1/mappings", headers=self.headers)
        self.assertEqual(mappings.status_code, 200)
        self.assertEqual(
            mappings.json()["supplier_aliases"]["PDF SUPPLIER NAME"],
            "BEVCO (FL)",
        )

    def test_item_mapping_is_verified_and_saved(self):
        saved = self.client.post(
            "/api/v1/mappings/items",
            headers=self.headers,
            json={
                "source_name": "PDF ITEM",
                "batch": "PDF-BATCH",
                "item_code": "B00025",
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["mapping_key"], "PDF ITEM|PDF-BATCH")
        mappings = self.client.get("/api/v1/mappings", headers=self.headers).json()
        self.assertEqual(mappings["item_mappings"]["PDF ITEM|PDF-BATCH"], "B00025")

    def test_erp_master_search_endpoints(self):
        suppliers = self.client.get(
            "/api/v1/masters/suppliers",
            headers=self.headers,
            params={"companycode": "2", "query": "BEVCO"},
        )
        self.assertEqual(suppliers.status_code, 200, suppliers.text)
        self.assertEqual(suppliers.json()["results"][0]["suppliercode"], "B00011")

        items = self.client.get(
            "/api/v1/masters/items",
            headers=self.headers,
            params={"query": "BAGPIPER"},
        )
        self.assertEqual(items.status_code, 200, items.text)
        self.assertEqual(items.json()["results"][0]["itemcode"], "B00025")

        stock = self.client.get(
            "/api/v1/masters/items/B00025/stock",
            headers=self.headers,
            params={"companycode": "2", "yearcode": "8"},
        )
        self.assertEqual(stock.status_code, 200, stock.text)
        self.assertFalse(stock.json()["configured"])

    def test_invalid_pdf_is_rejected(self):
        response = self.client.post(
            "/api/v1/extract",
            headers=self.headers,
            files={"pdf": ("not-a-pdf.pdf", b"not a pdf", "application/pdf")},
        )
        self.assertEqual(response.status_code, 415)

    def test_preview_insert_and_idempotent_replay(self):
        preview = self.client.post(
            "/api/v1/purchases/preview",
            headers=self.headers,
            json={
                "companycode": "2",
                "yearcode": "8",
                "invoice": INVOICE,
                "strict_total": True,
            },
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        preview_body = preview.json()
        self.assertTrue(preview_body["ready_for_insert"])
        self.assertEqual(preview_body["preview"]["suppliercode"], "B00011")

        insert = self.client.post(
            "/api/v1/purchases/insert",
            headers=self.headers,
            json={"approval_token": preview_body["approval_token"]},
        )
        self.assertEqual(insert.status_code, 200, insert.text)
        self.assertEqual(insert.json()["result"]["trnid"], 501)
        self.assertFalse(insert.json()["idempotent_replay"])

        replay = self.client.post(
            "/api/v1/purchases/insert",
            headers=self.headers,
            json={"approval_token": preview_body["approval_token"]},
        )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["idempotent_replay"])

    @patch("integration_api.main.extract_pdf", return_value=EXTRACTED)
    def test_pdf_upload_creates_database_validated_purchase_preview(self, _):
        response = self.client.post(
            "/api/v1/purchases/from-pdf/preview",
            headers=self.headers,
            data={"companycode": "2", "yearcode": "8", "strict_total": "true"},
            files={"pdf": ("invoice.pdf", b"%PDF-1.4 test", "application/pdf")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["ready_for_insert"])
        self.assertEqual(body["purchase_count"], 1)
        self.assertEqual(body["purchases"][0]["preview"]["suppliercode"], "B00011")
        self.assertEqual(body["purchases"][0]["preview"]["totamount"], "240.00")
        self.assertIn("approval_token", body["purchases"][0])

    @patch(
        "integration_api.main.inspect_purchase_mappings",
        return_value=[
            {
                "type": "item",
                "source": "UNKNOWN BEER",
                "mapping_key": "UNKNOWN BEER|B-1",
                "batch": "B-1",
                "message": "No match.",
            },
            {
                "type": "item",
                "source": "ANOTHER BEER",
                "mapping_key": "ANOTHER BEER|B-2",
                "batch": "B-2",
                "message": "No match.",
            },
        ],
    )
    @patch("integration_api.main.extract_pdf", return_value=EXTRACTED)
    def test_pdf_preview_returns_all_resolution_issues(self, _, __):
        response = self.client.post(
            "/api/v1/purchases/from-pdf/preview",
            headers=self.headers,
            data={"companycode": "2", "yearcode": "8", "strict_total": "true"},
            files={"pdf": ("invoice.pdf", b"%PDF-1.4 test", "application/pdf")},
        )
        self.assertEqual(response.status_code, 409, response.text)
        body = response.json()
        self.assertTrue(body["resolution_required"])
        self.assertEqual(body["unresolved_count"], 2)
        self.assertTrue(body["unresolved"][0]["actions"]["search_master"])
        self.assertTrue(body["unresolved"][0]["actions"]["check_live_stock"])

    @patch(
        "integration_api.main.extract_pdf",
        return_value={
            **EXTRACTED,
            "items": [{**EXTRACTED["items"][0], "batch": None}],
        },
    )
    def test_pdf_validation_error_includes_extraction_diagnostics(self, _):
        response = self.client.post(
            "/api/v1/purchases/from-pdf/preview",
            headers=self.headers,
            data={"companycode": "2", "yearcode": "8", "strict_total": "true"},
            files={"pdf": ("invoice.pdf", b"%PDF-1.4 test", "application/pdf")},
        )
        self.assertEqual(response.status_code, 422, response.text)
        detail = response.json()["detail"]
        self.assertIn("batch", detail["message"])
        self.assertEqual(detail["extraction_method"], "native_pdf_text_pymupdf")
        self.assertEqual(detail["items_extracted"], 1)


if __name__ == "__main__":
    unittest.main()
