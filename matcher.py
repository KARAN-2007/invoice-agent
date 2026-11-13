import json
from pathlib import Path

# Point to your folders
PO_DIR = Path("data/purchase_orders")
GRN_DIR = Path("data/goods_receipts")
INV_DIR = Path("data/invoices")
AUDIT_DIR = Path("data/audit_reports") # Folder where audit JSONs will be saved

def process_orders_sequentially():
    print("📂 Starting live order-by-order processing from folders...\n")

    if not INV_DIR.exists():
        print("⚠️ Invoices folder does not exist!")
        return
        
    # Ensure audit directory exists so it doesn't crash
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)

    seen_invoices = set()

    # 1. Read invoices ONE BY ONE
    for inv_file in INV_DIR.glob("*.json"):
        with open(inv_file, "r", encoding="utf-8") as f:
            invoice_data = json.load(f)

        vendor_id = invoice_data.get("vendor_id")
        po_id = invoice_data.get("po_id")
        invoice_number = invoice_data.get("invoice_number")
        invoice_amount = float(invoice_data.get("total_amount", 0))

        print(f"📄 Processing Order: Invoice #{invoice_number} (PO #{po_id})")

        discrepancies = []

        # 2. Check for Duplicate Invoice
        inv_key = (vendor_id, invoice_number)
        if inv_key in seen_invoices:
            discrepancies.append(("DUPLICATE_INVOICE", f"Invoice '{invoice_number}' was already submitted."))
        else:
            seen_invoices.add(inv_key)

        # 3. Fetch matching PO file on-demand
        po_data = None
        po_file = PO_DIR / f"po_{po_id}.json"
        if po_file.exists():
            with open(po_file, "r", encoding="utf-8") as pf:
                po_data = json.load(pf)
        else:
            discrepancies.append(("MISSING_PO", f"Purchase Order file for PO #{po_id} is missing."))

        # 4. Fetch matching GRN file on-demand
        grn_data = None
        grn_file = GRN_DIR / f"grn_{po_id}.json"
        if grn_file.exists():
            with open(grn_file, "r", encoding="utf-8") as gf:
                grn_data = json.load(gf)
        else:
            discrepancies.append(("MISSING_GRN", f"Goods Receipt file for PO #{po_id} is missing."))

        # 5. Run Price Variance Check (if PO is available)
        if po_data:
            po_amount = float(po_data.get("total_amount", 0))
            if invoice_amount > po_amount:
                diff = invoice_amount - po_amount
                discrepancies.append(("PRICE_HIKE", f"Invoice amount ({invoice_amount}) exceeds PO amount ({po_amount}) by {diff}."))

        # 6. Run GRN Status Check (if GRN is available)
        if grn_data:
            grn_status = grn_data.get("status")
            if grn_status != "RECEIVED":
                discrepancies.append(("GRN_NOT_RECEIVED", f"Goods Receipt status is '{grn_status}', not RECEIVED."))

        # 7. Save Audit Report to Disk for THIS ONE ORDER
        save_audit_report(invoice_number, discrepancies)
        print("-" * 50)

def save_audit_report(invoice_number, discrepancies):
    """Saves the individual order's audit report into a JSON file on disk."""
    audit_file = AUDIT_DIR / f"audit_{invoice_number}.json"
    
    status = "FLAGGED" if discrepancies else "APPROVED"
    report_data = {
        "invoice_number": invoice_number,
        "status": status,
        "discrepancies": [{"type": d[0], "details": d[1]} for d in discrepancies]
    }

    with open(audit_file, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=4)

    print(f"📋 Audit saved to disk: {audit_file} [{status}]")

if __name__ == "__main__":
    process_orders_sequentially()