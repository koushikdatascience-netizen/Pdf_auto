import unittest

from pdf_purchase_adapter import PdfPurchaseAdapterError, normalize_extracted_purchases


EXTRACTED = {
    "header": {"demand_id": "PDF-001", "date": "05/06/2026"},
    "items": [
        {
            "sl": 1,
            "label_name": "ITEM ONE",
            "batch": "B001",
            "capacity_ml": 750,
            "quantity_cases": 2,
            "mfg_amount": 200,
            "manufacturer": "SUPPLIER ONE",
        }
    ],
    "manufacturer_groups": [
        {
            "manufacturer": "SUPPLIER ONE",
            "item_sl_numbers": [1],
            "total_mfg_amount": 200,
            "total_vat": 40,
        }
    ],
}


class PdfPurchaseAdapterTests(unittest.TestCase):
    def test_normalizes_extracted_pdf_to_purchase_invoice(self):
        invoices = normalize_extracted_purchases(EXTRACTED)
        self.assertEqual(len(invoices), 1)
        self.assertEqual(invoices[0]["supplier"], "SUPPLIER ONE")
        self.assertEqual(invoices[0]["items"][0]["item_name"], "ITEM ONE")
        self.assertEqual(invoices[0]["items"][0]["rate"], "100.00")
        self.assertEqual(invoices[0]["total"], "240.00")

    def test_rejects_unassociated_product(self):
        extracted = {**EXTRACTED, "manufacturer_groups": []}
        with self.assertRaises(PdfPurchaseAdapterError):
            normalize_extracted_purchases(extracted)


if __name__ == "__main__":
    unittest.main()
