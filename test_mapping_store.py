import tempfile
import unittest
from pathlib import Path

from integration_api.mapping_store import MappingStore
from mapping_service import MappingConfig, item_mapping_key
from validation import ValidatedItem
from decimal import Decimal


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

    def test_product_mapping_key_ignores_batch(self):
        first = ValidatedItem(
            "Stok Strong Beer", None, "B001", Decimal("650.00"), None, None,
            Decimal("1"), Decimal("1"), Decimal("1")
        )
        second = ValidatedItem(
            "STOK  STRONG BEER", None, "B999", Decimal("650.00"), None, None,
            Decimal("1"), Decimal("1"), Decimal("1")
        )
        self.assertEqual(item_mapping_key(first), item_mapping_key(second))
        self.assertEqual(item_mapping_key(first), "STOK STRONG BEER|ML:650.00")


if __name__ == "__main__":
    unittest.main()
