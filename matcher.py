import os
import psycopg2
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

DB_NAME = os.getenv("DB_NAME", "invoice_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")

def run_three_way_matching():
    try:
        connection = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT
        )
        cursor = connection.cursor()
        print("Connected to PostgreSQL. Running 3-Way Matching Engine...\n")

        # Fetch all pending invoices along with their associated PO total and GRN status
        query = """
            SELECT 
                i.id AS invoice_id,
                i.vendor_id,
                i.po_id,
                i.total_amount AS invoice_amount,
                po.total_amount AS po_amount,
                g.status AS grn_status
            FROM invoices i
            JOIN purchase_orders po ON i.po_id = po.id
            LEFT JOIN goods_receipts g ON i.po_id = g.po_id
            WHERE i.status = 'PENDING';
        """
        cursor.execute(query)
        pending_invoices = cursor.fetchall()

        matched_count = 0
        flagged_count = 0

        for inv in pending_invoices:
            invoice_id, vendor_id, po_id, invoice_amount, po_amount, grn_status = inv
            discrepancies = []

            # Rule 1: Check if Goods Receipt exists and is RECEIVED
            if not grn_status or grn_status != 'RECEIVED':
                discrepancies.append(("MISSING_GRN", f"Goods Receipt not found or not received for PO #{po_id}"))

            # Rule 2: Check for Price / Amount Mismatch (Tolerance threshold: exact match or minor variance)
            if float(invoice_amount) > float(po_amount):
                diff = float(invoice_amount) - float(po_amount)
                discrepancies.append(("PRICE_HIKE", f"Invoice amount ({invoice_amount}) exceeds PO amount ({po_amount}) by {diff}"))

            if discrepancies:
                flagged_count += 1
                print(f"⚠️ [FLAGGED] Invoice #{invoice_id} (PO #{po_id}) has issues:")
                for disc_type, details in discrepancies:
                    print(f"   - {disc_type}: {details}")
                    # Insert into audit_logs table
                    cursor.execute("""
                        INSERT INTO audit_logs (invoice_id, discrepancy_type, details)
                        VALUES (%s, %s, %s);
                    """, (invoice_id, disc_type, details))
            else:
                matched_count += 1
                print(f"✅ [MATCHED] Invoice #{invoice_id} matches PO #{po_id} perfectly.")
                # Update invoice status to APPROVED or PAID
                cursor.execute("""
                    UPDATE invoices SET status = 'APPROVED' WHERE id = %s;
                """, (invoice_id,))

        connection.commit()
        print(f"\n🎯 Matching Complete: {matched_count} approved, {flagged_count} flagged discrepancies logged to audit_logs.")

    except Exception as e:
        print(f"Error running matching engine: {e}")
        if 'connection' in locals():
            connection.rollback()
    finally:
        if 'cursor' in locals(): cursor.close()
        if 'connection' in locals(): connection.close()

if __name__ == "__main__":
    run_three_way_matching()