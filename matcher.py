import json
from datetime import datetime
from pathlib import Path

# Point to your folders
PO_DIR = Path("data/purchase_orders")
GRN_DIR = Path("data/goods_receipts")
INV_DIR = Path("data/invoices")
AUDIT_DIR = Path("data/audit_reports")

def process_orders_sequentially():
    print("📂 Starting live order-by-order processing (with timestamped audit files)...\n")

    if not INV_DIR.exists():
        print("⚠️ Invoices folder does not exist!")
        return
        
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    seen_invoices = set()

    for inv_file in INV_DIR.glob("*.json"):
        with open(inv_file, "r", encoding="utf-8") as f:
            invoice_data = json.load(f)

        vendor_id = invoice_data.get("vendor_id")
        po_id = invoice_data.get("po_id")
        invoice_number = invoice_data.get("invoice_number")
        invoice_items = invoice_data.get("items", [])

        # 1. CHECK IF ANY AUDIT REPORT FOR THIS INVOICE ALREADY EXISTS
        existing_audits = list(AUDIT_DIR.glob(f"audit_{invoice_number}_*.json"))
        if existing_audits:
            print(f"⏩ Skipping Invoice #{invoice_number} (Audit report already exists on disk).")
            continue

        print(f"📄 Processing New Order: Invoice #{invoice_number} (PO #{po_id})")

        discrepancies = []

        # 2. Duplicate Invoice Check
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

        # 5. Header-level & Item-level Matching (if PO exists)
        if po_data:
            po_total = float(po_data.get("total_amount", 0))
            inv_total = float(invoice_data.get("total_amount", 0))

            if inv_total > po_total:
                discrepancies.append(("PRICE_HIKE", f"Invoice total ({inv_total}) exceeds PO total ({po_total})."))

            po_items_map = {item["sku"]: item for item in po_data.get("items", [])}

            for inv_item in invoice_items:
                sku = inv_item.get("sku")
                inv_qty = inv_item.get("quantity", 0)
                inv_price = inv_item.get("unit_price", 0)

                if sku not in po_items_map:
                    discrepancies.append(("UNKNOWN_SKU", f"Item SKU '{sku}' on invoice was not found on PO #{po_id}."))
                else:
                    po_item = po_items_map[sku]
                    po_qty = po_item.get("quantity", 0)
                    po_price = po_item.get("unit_price", 0)

                    if inv_qty > po_qty:
                        discrepancies.append(("QUANTITY_MISMATCH", f"Invoiced qty ({inv_qty}) for '{sku}' exceeds PO qty ({po_qty})."))

                    if inv_price > po_price:
                        discrepancies.append(("UNIT_PRICE_HIKE", f"Invoiced unit price ({inv_price}) for '{sku}' exceeds PO price ({po_price})."))

        # 6. GRN Status Check (if GRN exists)
        if grn_data:
            grn_status = grn_data.get("status")
            if grn_status != "RECEIVED":
                discrepancies.append(("GRN_NOT_RECEIVED", f"Goods Receipt status is '{grn_status}', not RECEIVED."))

        # 7. Save Timestamped Audit Report to Disk
        save_audit_report(invoice_number, discrepancies)
        print("-" * 50)

def save_audit_report(invoice_number, discrepancies):
    # Generate current date and time string (e.g., "2026-11-15_02-58-12")
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    audit_file = AUDIT_DIR / f"audit_{invoice_number}_{timestamp}.json"
    
    status = "FLAGGED" if discrepancies else "APPROVED"
    report_data = {
        "invoice_number": invoice_number,
        "status": status,
        "generated_at": timestamp,
        "discrepancies": [{"type": d[0], "details": d[1]} for d in discrepancies]
    }

    with open(audit_file, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=4)

    print(f"📋 Timestamped audit saved to disk: {audit_file.name} [{status}]")

if __name__ == "__main__":
    process_orders_sequentially()