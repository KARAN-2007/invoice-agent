
import unittest

from approval_evidence import build_approval_evidence


class ApprovalEvidenceTests(unittest.TestCase):
    FILE = "invoice-demo.pdf"

    def event(self, step, result, event_id):
        return {
            "id": event_id,
            "step": step,
            "detail": {
                "file": self.FILE,
                "result": result,
            },
        }

    def test_unmapped_po_is_not_marked_passed(self):
        rows = [
            self.event("tool:read_invoice", {"invoice_number": "US-001"}, 1),
            self.event("tool:check_invoice_math", {"math_ok": True}, 2),
            self.event("tool:lookup_po", {"status": "not_found", "po": None}, 3),
            self.event("tool:check_dataset_rules", {"findings": ["PO_REFERENCE_UNMAPPED"]}, 4),
        ]

        cards = {
            item["name"]: item
            for item in build_approval_evidence(self.FILE, rows)
        }

        self.assertEqual(cards["Arithmetic"]["status"], "Passed")
        self.assertEqual(cards["PO lookup"]["status"], "Not found")
        self.assertEqual(cards["Vendor matching"]["status"], "Not run")
        self.assertEqual(cards["GRN verification"]["status"], "Not assessed")
        self.assertEqual(cards["Budget validation"]["status"], "Not verified")

    def test_found_po_and_vendor_match(self):
        rows = [
            self.event("tool:lookup_po", {"status": "found", "po": {"po_number": "PO-123"}}, 1),
            self.event("tool:check_vendor_matches_po", {"status": "match"}, 2),
            self.event("tool:check_dataset_rules", {"findings": ["GRN_QUANTITY_SHORTAGE"]}, 3),
        ]

        cards = {
            item["name"]: item
            for item in build_approval_evidence(self.FILE, rows)
        }

        self.assertEqual(cards["PO lookup"]["status"], "Found · PO-123")
        self.assertEqual(cards["Vendor matching"]["status"], "Matched")
        self.assertEqual(cards["GRN verification"]["status"], "Quantity shortage")
        self.assertEqual(cards["Budget validation"]["status"], "Not verified")

    def test_other_invoice_events_are_ignored(self):
        rows = [{
            "id": 1,
            "step": "tool:check_invoice_math",
            "detail": {
                "file": "other.pdf",
                "result": {"math_ok": True},
            },
        }]

        cards = {
            item["name"]: item
            for item in build_approval_evidence(self.FILE, rows)
        }

        self.assertEqual(cards["Arithmetic"]["status"], "Not recorded")


if __name__ == "__main__":
    unittest.main()
