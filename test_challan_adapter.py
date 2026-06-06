import unittest

from challan_adapter import AdapterError, adapt_challan, build_mapping_template, item_override_key
from extract_pdf_json import parse_items


class ChallanAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        group_sizes = [1, 5, 4, 3, 2]
        items = []
        groups = []
        serial = 1
        for group_number, size in enumerate(group_sizes, start=1):
            manufacturer = f"Manufacturer {group_number}"
            serials = list(range(serial, serial + size))
            group_items = []
            for item_serial in serials:
                amount = 16900 if item_serial == 1 else 100
                group_items.append(
                    {
                        "sl": item_serial,
                        "label_name": (
                            "Rio Red Strong Party Drink"
                            if item_serial == 1
                            else f"Product {item_serial}"
                        ),
                        "batch": f"BATCH-{item_serial}",
                        "capacity_ml": 330,
                        "package": "Glass Bottle",
                        "quantity_cases": 10 if item_serial == 1 else 1,
                        "mfg_amount": amount,
                        "manufacturer": manufacturer,
                    }
                )
            items.extend(group_items)
            groups.append(
                {
                    "manufacturer": manufacturer,
                    "item_sl_numbers": serials,
                    "total_duty": 12900 if group_number == 1 else 0,
                    "total_mfg_amount": sum(item["mfg_amount"] for item in group_items),
                    "total_vat": 3984 if group_number == 1 else 0,
                }
            )
            serial += size
        cls.challan = {
            "header": {"demand_id": "tFLDR/2026-2027/00040692", "date": "16/04/2026"},
            "items": items,
            "manufacturer_groups": groups,
        }

    def test_splits_real_challan_by_manufacturer(self):
        purchases = adapt_challan(
            self.challan,
            companycode="C01",
            yearcode="2026-27",
            item_resolver=lambda item: f"ITEM{item['sl']:03d}",
        )

        self.assertEqual(len(purchases), 5)
        self.assertEqual(len(purchases[0]["items"]), 1)
        self.assertEqual(len(purchases[1]["items"]), 5)
        self.assertEqual(len(purchases[2]["items"]), 4)
        self.assertEqual(len(purchases[3]["items"]), 3)
        self.assertEqual(len(purchases[4]["items"]), 2)
        self.assertEqual(purchases[0]["invoice_no"], "tFLDR/2026-2027/00040692-01")
        self.assertEqual(purchases[0]["items"][0]["name"], "ITEM001")
        self.assertEqual(purchases[0]["items"][0]["price"], "1690.00")
        self.assertEqual(purchases[0]["tax"]["gst_amount"], "3984.00")
        self.assertEqual(purchases[0]["total"], "20884.00")

    def test_all_source_items_covered_once(self):
        purchases = adapt_challan(
            self.challan,
            companycode="C01",
            yearcode="2026-27",
            item_resolver=lambda item: f"ITEM{item['sl']:03d}",
        )
        mapped_batches = [
            item["batch"]
            for purchase in purchases
            for item in purchase["items"]
        ]
        self.assertEqual(len(mapped_batches), 15)
        self.assertEqual(len(set(mapped_batches)), 15)

    def test_supplier_alias_is_applied(self):
        manufacturer = self.challan["manufacturer_groups"][0]["manufacturer"]
        purchases = adapt_challan(
            self.challan,
            companycode="C01",
            yearcode="2026-27",
            item_resolver=lambda item: f"ITEM{item['sl']:03d}",
            supplier_aliases={manufacturer: "GOODDROP"},
        )
        self.assertEqual(purchases[0]["supplier"], "GOODDROP")

    def test_blank_supplier_alias_falls_back_to_extracted_name(self):
        manufacturer = self.challan["manufacturer_groups"][0]["manufacturer"]
        purchases = adapt_challan(
            self.challan,
            companycode="C01",
            yearcode="2026-27",
            item_resolver=lambda item: f"ITEM{item['sl']:03d}",
            supplier_aliases={manufacturer: ""},
        )
        self.assertEqual(purchases[0]["supplier"], manufacturer)

    def test_mapping_template_covers_all_masters(self):
        template = build_mapping_template(self.challan)
        self.assertEqual(len(template["supplier_aliases"]), 5)
        self.assertEqual(len(template["item_overrides"]), 15)

    def test_item_override_key(self):
        self.assertEqual(
            item_override_key(self.challan["items"][0]),
            "Rio Red Strong Party Drink|330|Glass Bottle|BATCH-1",
        )

    def test_missing_group_item_is_rejected(self):
        challan = {
            **self.challan,
            "manufacturer_groups": [
                {**group, "item_sl_numbers": list(group["item_sl_numbers"])}
                for group in self.challan["manufacturer_groups"]
            ],
        }
        challan["manufacturer_groups"][0]["item_sl_numbers"] = [999]
        with self.assertRaises(AdapterError):
            adapt_challan(
                challan,
                companycode="C01",
                yearcode="2026-27",
                item_resolver=lambda item: "ITEM001",
            )

    def test_extracts_manufacturer_without_known_prefix(self):
        lines = [
            "1",
            "GENERIC PRODUCT [FL/2026-2027/0001]",
            "750",
            "(Glass",
            "Bottle)",
            "2",
            "10.00",
            "200.00",
            "40.00",
            "ABC TRADERS PRIVATE LIMITED",
            "INDUSTRIAL AREA CITY",
            "Total:",
            "2",
            "10.00",
            "200.00",
            "40.00",
            "Grand Total",
        ]
        items, groups = parse_items(lines)
        self.assertEqual(len(items), 1)
        self.assertEqual(len(groups), 1)
        self.assertEqual(
            groups[0]["manufacturer"],
            "ABC TRADERS PRIVATE LIMITED INDUSTRIAL AREA CITY",
        )

    def test_extracts_non_fl_batch_prefix(self):
        lines = [
            "1",
            "STRONG BEER [BR/2026-2027/0042]",
            "650",
            "(Glass",
            "Bottle)",
            "5",
            "10.00",
            "500.00",
            "100.00",
            "BREWERY PRIVATE LIMITED",
            "Total:",
            "5",
            "10.00",
            "500.00",
            "100.00",
            "Grand Total",
        ]
        items, groups = parse_items(lines)
        self.assertEqual(items[0]["label_name"], "STRONG BEER")
        self.assertEqual(items[0]["batch"], "BR/2026-2027/0042")
        self.assertEqual(groups[0]["manufacturer"], "BREWERY PRIVATE LIMITED")


if __name__ == "__main__":
    unittest.main()
