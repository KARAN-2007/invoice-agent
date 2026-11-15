import json
from datetime import datetime
from pathlib import Path

# Point to your folders
PO_DIR = Path("data/purchase_orders")
GRN_DIR = Path("data/goods_receipts")
INV_DIR = Path("data/invoices")
AUDIT_DIR = Path("data/audit_reports")

def process_orders_sequentially():
    print("📂 Starting live order-by-order processing (with PO-driven dynamic schema matching)...\n")

    if not INV_DIR.exists():
        print("⚠️ Invoices folder does not exist!")
        return
        
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    seen_invoices = set()

    for inv_file in INV_DIR.glob("*.json"):
        with open(inv_file, "r", encoding="utf-8") as f:
            invoice_data = json.load(f)

        vendor_id = invoice_data.get("vendor_id") or invoice_data.get("vid")
        po_id = invoice_data.get("po_id") or invoice_data.get("purchase_order_id")
        invoice_number = invoice_data.get("invoice_number") or invoice_data.get("inv_no")
        
        # Safe extraction for invoice items (supporting multiple alias keys)
        invoice_items = invoice_data.get("items", []) or invoice_data.get("line_items", [])

        # Debugging start
        print("\nDEBUGGING")
        print("Invoice file:", inv_file.name)
        print("Extracted PO ID:", repr(po_id))
        print("PO directory:", PO_DIR.resolve())
        print("PO directory contents:", [f.name for f in PO_DIR.glob("*")])
        print("GRN directory:", GRN_DIR.resolve())
        print("GRN directory contents:", [f.name for f in GRN_DIR.glob("*")])
        #Debuggging end
        
        # 1. Check if audit report already exists
        if not invoice_number:
            print(f"⚠️ Skipping file {inv_file.name}: Missing invoice number identifier.")
            continue

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

        # 5. PO-Driven Dynamic Schema & Item Matching
        if po_data:
            po_total = float(po_data.get("total_amount") or po_data.get("total") or 0)
            inv_total = float(invoice_data.get("total_amount") or invoice_data.get("total") or 0)

            if inv_total > po_total:
                discrepancies.append(("PRICE_HIKE", f"Invoice total ({inv_total}) exceeds PO total ({po_total})."))

            po_items = po_data.get("items", []) or po_data.get("line_items", [])

            if po_items:
                # Detect the exact column keys used in THIS specific PO
                sample_po_item = po_items[0]
                sku_key = next((k for k in ["sku", "item_code", "product_id", "item_no"] if k in sample_po_item), "sku")
                qty_key = next((k for k in ["quantity", "qty", "count"] if k in sample_po_item), "quantity")
                price_key = next((k for k in ["unit_price", "price", "rate", "cost"] if k in sample_po_item), "unit_price")

                # Build lookup map using the PO's detected keys
                po_items_map = {str(item.get(sku_key)): item for item in po_items}

                for inv_item in invoice_items:
                    # Dynamically match invoice item keys using the PO's schema pattern
                    inv_sku = str(inv_item.get(sku_key) or inv_item.get("sku") or inv_item.get("item_code"))
                    inv_qty = float(inv_item.get(qty_key) or inv_item.get("quantity") or inv_item.get("qty") or 0)
                    inv_price = float(inv_item.get(price_key) or inv_item.get("unit_price") or inv_item.get("price") or 0)

                    if inv_sku not in po_items_map:
                        discrepancies.append(("UNKNOWN_SKU", f"Item identifier '{inv_sku}' on invoice was not found on PO #{po_id}."))
                    else:
                        po_item = po_items_map[inv_sku]
                        po_qty = float(po_item.get(qty_key, 0))
                        po_price = float(po_item.get(price_key, 0))

                        if inv_qty > po_qty:
                            discrepancies.append(("QUANTITY_MISMATCH", f"Invoiced qty ({inv_qty}) for '{inv_sku}' exceeds PO qty ({po_qty})."))

                        if inv_price > po_price:
                            discrepancies.append(("UNIT_PRICE_HIKE", f"Invoiced unit price ({inv_price}) for '{inv_sku}' exceeds PO price ({po_price})."))

        # 6. GRN Status Check (if GRN exists)
        if grn_data:
            grn_status = grn_data.get("status") or grn_data.get("receipt_status")
            if grn_status != "RECEIVED":
                discrepancies.append(("GRN_NOT_RECEIVED", f"Goods Receipt status is '{grn_status}', not RECEIVED."))

        # 7. Save Timestamped Audit Report to Disk
        save_audit_report(invoice_number, discrepancies)
        print("-" * 50)

def save_audit_report(invoice_number, discrepancies):
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

    print(f"📋 PO-schema audit saved to disk: {audit_file.name} [{status}]")

if __name__ == "__main__":
    process_orders_sequentially()
