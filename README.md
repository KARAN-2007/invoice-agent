# invoice-agent
Buildathon project
#test.py- tried with gemini 3.8 but server was full, tried 2.5 flash, it worked
#json_test.py - tried with mupltiple models, worked with gemini 3.1 flash lite, switched the model in test.py too
#invoice_extraction- worked with pdf , now trying with different data types

#Core Pipeline Components backend We Built
1. **`schema.sql` & `db_setup.py`:** Initializes the normalized relational schema (`vendors`, `purchase_orders`, `goods_receipts`, `invoices`, `audit_logs`) with foreign key constraints and cascade operations.
2. **`seed_db.py`:** Idempotent seeder injecting baseline master data alongside trap cases (price hikes, quantity mismatches, unapproved POs).
3. **`matcher.py`:** Deterministic 3-way matching engine evaluating PO vs. GRN vs. Invoice tolerances and flagging discrepancies.
4. **`audit_report.py`:** Queries PostgreSQL to generate structured compliance audit logs and discrepancy summaries.
5. **`ingest_api.py`:** API ingestion bridge accepting incoming JSON invoice payloads and triggering real-time matching.
