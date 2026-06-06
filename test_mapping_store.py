import tempfile
import unittest
from pathlib import Path

from integration_api.mapping_store import MappingStore
from mapping_service import MappingConfig


class MappingStoreTests(unittest.TestCase):
    def test_persists_and_merges_runtime_mappings(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "mappings.json"
            store = MappingStore(path)
            store.set_supplier("PDF SUPPLIER", "ERP SUPPLIER")
            store.set_item("PDF ITEM|B001", "ITEM001")

            reloaded = MappingStore(path)
            config = reloaded.snapshot(
                MappingConfig({"CONFIG SUPPLIER": "ERP CONFIG"}, {})
            )
            self.assertEqual(config.supplier_aliases["PDF SUPPLIER"], "ERP SUPPLIER")
            self.assertEqual(config.supplier_aliases["CONFIG SUPPLIER"], "ERP CONFIG")
            self.assertEqual(config.item_mappings["PDF ITEM|B001"], "ITEM001")


if __name__ == "__main__":
    unittest.main()
