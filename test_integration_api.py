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


class FakeCursor:
    def __init__(self):
        self.result = []
        self.closed = False
        self.detail_rows = []

    def execute(self, sql, *params):
        normalized = " ".join(sql.upper().split())
        if "MASTERACCOUNTSLEDGER" in normalized:
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
            supplier_lookup_sql=(
                "SELECT ledgerCode FROM dbo.MasterAccountsLedger "
                "WHERE ledgerName=? AND companyCode=?"
            ),
            item_lookup_sql="SELECT itemcode FROM dbo.itemmst WHERE itemname=?",
            item_code_verify_sql="SELECT itemcode FROM dbo.itemmst WHERE itemcode=?",
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


if __name__ == "__main__":
    unittest.main()
