import json
from pathlib import Path

import streamlit as st


BASE_DIR = Path(__file__).resolve().parent
REFERENCE_FILE = BASE_DIR / "dataset" / "reference_data.json"


# ==================================================
# DATA HELPERS
# ==================================================

def load_procurement_data():
    """Load the existing procurement reference data without modifying it."""

    try:
        with REFERENCE_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, dict):
            raise ValueError("Invalid procurement reference data.")

        return data

    except FileNotFoundError:
        st.error("dataset/reference_data.json was not found.")
        return {}

    except (OSError, json.JSONDecodeError, ValueError) as exc:
        st.error("Could not load procurement data.")
        st.caption(str(exc)[:200])
        return {}


def get_po_vendor(po):
    return (
        po.get("vendor_name")
        or po.get("vendor")
        or "Unknown vendor"
    )


def get_po_number(po):
    return str(po.get("po_number") or "Not assigned")


def get_status(po):
    return str(po.get("status") or "UNKNOWN").upper()


def get_items(po):
    items = po.get("items") or []

    if not isinstance(items, list):
        return []

    return [
        item for item in items
        if isinstance(item, dict)
    ]


def format_amount(value, currency="INR"):
    try:
        return f"{currency} {float(value):,.2f}"
    except (ValueError, TypeError):
        return "Not specified"


def get_vendor_options(reference):
    vendors = reference.get("vendors", [])

    names = set()

    if isinstance(vendors, list):
        for vendor in vendors:
            if isinstance(vendor, dict):
                name = vendor.get("name") or vendor.get("vendor_name")

                if name:
                    names.add(str(name))

    purchase_orders = reference.get("purchase_orders", [])

    if isinstance(purchase_orders, list):
        for po in purchase_orders:
            if isinstance(po, dict):
                names.add(str(get_po_vendor(po)))

    names.discard("Unknown vendor")

    return sorted(names)


def render_status(status):
    status = str(status).upper()

    if status == "APPROVED":
        st.success(status)

    elif status in ("PENDING", "SUBMITTED", "DRAFT"):
        st.warning(status)

    elif status in ("CANCELLED", "REJECTED"):
        st.error(status)

    elif status in ("COMPLETED", "CLOSED"):
        st.info(status)

    else:
        st.caption(status)


def get_dataframe_height(row_count, max_height=900):
    """Calculate table height based on the number of rows."""

    return min(
        max_height,
        max(200, 38 * (row_count + 1)),
    )


# ==================================================
# PO DETAILS
# ==================================================

def render_po_details(po):
    st.markdown("### Purchase order details")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric("PO Number", get_po_number(po))

    with col2:
        st.metric("Vendor", get_po_vendor(po))

    with col3:
        st.metric("Line items", len(get_items(po)))

    currency = str(po.get("currency") or "INR")

    st.markdown("**Order information**")

    info1, info2, info3 = st.columns(3)

    with info1:
        st.write("**Vendor ID**")
        st.write(str(po.get("vendor_id") or "-"))

    with info2:
        st.write("**Currency**")
        st.write(currency)

    with info3:
        st.write("**Price tolerance**")

        tolerance = po.get("price_tolerance_percent")

        st.write(
            str(tolerance) + "%"
            if tolerance is not None
            else "-"
        )

    st.markdown("**Order status**")
    render_status(get_status(po))

    st.divider()

    st.markdown("### Ordered items")

    items = get_items(po)

    if not items:
        st.info("No line items are recorded for this purchase order.")
        return

    rows = []

    for item in items:
        rows.append({
            "Description": item.get("description") or "-",
            "Quantity ordered": item.get("quantity_ordered", "-"),
            "Approved unit price": format_amount(
                item.get("approved_unit_price"),
                currency,
            ),
            "Tax rate": (
                str(item.get("tax_percent")) + "%"
                if item.get("tax_percent") is not None
                else "-"
            ),
        })

    st.dataframe(
        rows,
        use_container_width=True,
        hide_index=True,
        height=get_dataframe_height(len(rows)),
    )


# ==================================================
# DASHBOARD
# ==================================================

def render_po_dashboard(purchase_orders):
    st.markdown("### Procurement overview")

    total = len(purchase_orders)

    approved = sum(
        1 for po in purchase_orders
        if get_status(po) == "APPROVED"
    )

    pending = sum(
        1 for po in purchase_orders
        if get_status(po) in ("PENDING", "SUBMITTED", "DRAFT")
    )

    cancelled = sum(
        1 for po in purchase_orders
        if get_status(po) in ("CANCELLED", "REJECTED")
    )

    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Total purchase orders", total)
    col2.metric("Approved", approved)
    col3.metric("Pending", pending)
    col4.metric("Cancelled / rejected", cancelled)

    st.divider()

    # IMPORTANT: Do not use purchase_orders[:8].
    # The dashboard must receive the complete PO list.

    st.markdown("### All purchase orders")

    if not purchase_orders:
        st.info("No purchase orders are available.")
        return

    rows = []

    for po in purchase_orders:
        rows.append({
            "PO Number": get_po_number(po),
            "Vendor": get_po_vendor(po),
            "Status": get_status(po),
            "Currency": po.get("currency") or "-",
            "Line items": len(get_items(po)),
        })

    st.caption(f"Showing all {len(rows)} purchase orders.")

    st.dataframe(
        rows,
        use_container_width=True,
        hide_index=True,
        height=get_dataframe_height(len(rows)),
    )


# ==================================================
# PURCHASE ORDER LIST
# ==================================================

def render_po_list(purchase_orders):
    st.markdown("### All purchase orders")

    search = st.text_input(
        "Search purchase orders",
        placeholder="Search PO number or vendor...",
        key="po_search",
    ).strip().lower()

    statuses = sorted({
        get_status(po)
        for po in purchase_orders
    })

    status_options = ["All statuses"] + statuses

    selected_status = st.selectbox(
        "Filter by status",
        status_options,
        key="po_status_filter",
    )

    vendors = sorted({
        get_po_vendor(po)
        for po in purchase_orders
    })

    vendor_options = ["All vendors"] + vendors

    selected_vendor = st.selectbox(
        "Filter by vendor",
        vendor_options,
        key="po_vendor_filter",
    )

    filtered = []

    for po in purchase_orders:
        po_number = get_po_number(po).lower()
        vendor = get_po_vendor(po).lower()

        if search and search not in po_number and search not in vendor:
            continue

        if (
            selected_status != "All statuses"
            and get_status(po) != selected_status
        ):
            continue

        if (
            selected_vendor != "All vendors"
            and get_po_vendor(po) != selected_vendor
        ):
            continue

        filtered.append(po)

    st.caption(f"{len(filtered)} purchase orders found.")

    if not filtered:
        st.info("No purchase orders match your filters.")
        return

    table = []

    for po in filtered:
        table.append({
            "PO Number": get_po_number(po),
            "Vendor": get_po_vendor(po),
            "Status": get_status(po),
            "Currency": po.get("currency") or "-",
            "Line items": len(get_items(po)),
        })

    # Display every matching PO, not just the first few.
    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
        height=get_dataframe_height(len(table)),
    )

    st.divider()

    po_numbers = [
        get_po_number(po)
        for po in filtered
    ]

    selected_po = st.selectbox(
        "Select a PO to inspect",
        po_numbers,
        key="po_detail_selection",
    )

    selected_record = next(
        po for po in filtered
        if get_po_number(po) == selected_po
    )

    render_po_details(selected_record)


# ==================================================
# CREATE PURCHASE ORDER - PREVIEW
# ==================================================

def render_create_po(reference):
    st.markdown("### Create a purchase order")

    st.info(
        "Preview mode: The database CRUD functions are not connected yet. "
        "You can prepare and inspect a draft, but it will not be saved."
    )

    vendor_options = get_vendor_options(reference)

    if not vendor_options:
        st.warning(
            "No vendor records were found. You can still enter a vendor name "
            "for this temporary preview."
        )

    item_count = st.number_input(
        "Number of line items",
        min_value=1,
        max_value=8,
        value=1,
        step=1,
        key="po_item_count",
    )

    with st.form("create_po_preview_form"):

        st.markdown("#### 1. Vendor details")

        if vendor_options:
            vendor_name = st.selectbox(
                "Select vendor",
                vendor_options,
            )
        else:
            vendor_name = st.text_input("Vendor name")

        currencies = sorted({
            str(po.get("currency") or "INR")
            for po in reference.get("purchase_orders", [])
            if isinstance(po, dict)
        })

        if not currencies:
            currencies = ["INR"]

        if "INR" not in currencies:
            currencies.insert(0, "INR")

        currency = st.selectbox(
            "Currency",
            currencies,
        )

        tolerance = st.number_input(
            "Price tolerance (%)",
            min_value=0.0,
            max_value=100.0,
            value=1.0,
            step=0.25,
        )

        st.divider()

        st.markdown("#### 2. Purchase order items")

        draft_items = []

        for index in range(int(item_count)):
            st.markdown(f"**Item {index + 1}**")

            description = st.text_input(
                "Item description",
                key=f"po_item_description_{index}",
            )

            col1, col2, col3 = st.columns(3)

            with col1:
                quantity = st.number_input(
                    "Quantity",
                    min_value=0.0,
                    value=1.0,
                    step=1.0,
                    key=f"po_item_quantity_{index}",
                )

            with col2:
                unit_price = st.number_input(
                    "Unit price",
                    min_value=0.0,
                    value=0.0,
                    step=100.0,
                    key=f"po_item_price_{index}",
                )

            with col3:
                tax_percent = st.number_input(
                    "Tax (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=18.0,
                    step=0.5,
                    key=f"po_item_tax_{index}",
                )

            draft_items.append({
                "description": description.strip(),
                "quantity_ordered": quantity,
                "approved_unit_price": unit_price,
                "tax_percent": tax_percent,
            })

            st.divider()

        submitted = st.form_submit_button(
            "Generate PO preview",
            type="primary",
            use_container_width=True,
        )

    if submitted:

        if not vendor_name.strip():
            st.error("Please provide a vendor name.")

        elif any(
            not item["description"]
            or item["quantity_ordered"] <= 0
            or item["approved_unit_price"] <= 0
            for item in draft_items
        ):
            st.error(
                "Every item needs a description, positive quantity "
                "and positive unit price."
            )

        else:
            subtotal = sum(
                item["quantity_ordered"]
                * item["approved_unit_price"]
                for item in draft_items
            )

            tax_total = sum(
                item["quantity_ordered"]
                * item["approved_unit_price"]
                * item["tax_percent"] / 100
                for item in draft_items
            )

            st.session_state["po_draft_preview"] = {
                "vendor": vendor_name,
                "currency": currency,
                "tolerance": tolerance,
                "items": draft_items,
                "subtotal": subtotal,
                "tax": tax_total,
                "total": subtotal + tax_total,
            }

    # Display generated draft preview.

    draft = st.session_state.get("po_draft_preview")

    if draft:
        st.divider()

        st.markdown("### Purchase order preview")

        st.warning(
            "This is a temporary preview only. "
            "No PO number has been assigned and no database record has been created."
        )

        c1, c2, c3 = st.columns(3)

        c1.metric("Vendor", draft["vendor"])
        c2.metric("Line items", len(draft["items"]))
        c3.metric(
            "Price tolerance",
            str(draft["tolerance"]) + "%",
        )

        st.markdown("**Items**")

        preview_rows = []

        for item in draft["items"]:
            preview_rows.append({
                "Description": item["description"],
                "Quantity": item["quantity_ordered"],
                "Unit price": format_amount(
                    item["approved_unit_price"],
                    draft["currency"],
                ),
                "Tax": str(item["tax_percent"]) + "%",
            })

        st.dataframe(
            preview_rows,
            use_container_width=True,
            hide_index=True,
            height=get_dataframe_height(len(preview_rows)),
        )

        st.markdown("**Financial summary**")

        st.write(
            "Line subtotal before tax: "
            + format_amount(
                draft["subtotal"],
                draft["currency"],
            )
        )

        st.write(
            "Calculated tax: "
            + format_amount(
                draft["tax"],
                draft["currency"],
            )
        )

        st.subheader(
            "Preview total: "
            + format_amount(
                draft["total"],
                draft["currency"],
            )
        )


# ==================================================
# MAIN PO PAGE
# ==================================================

def render_po_page(user):
    """Render the purchase order management interface."""

    role = str(user.get("role") or "").strip().lower()

    if role != "procurement_officer":
        st.error(
            "You do not have permission to manage purchase orders."
        )
        st.caption("Detected role: " + repr(role))
        return

    st.title("Purchase order management")

    st.caption(
        "Create, inspect and track university purchase orders."
    )

    st.info(
        "Procurement data is currently displayed in read-only preview mode. "
        "PostgreSQL integration will be connected when the PO backend is ready."
    )

    reference = load_procurement_data()

    purchase_orders = reference.get("purchase_orders", [])

    if not isinstance(purchase_orders, list):
        purchase_orders = []

    purchase_orders = [
        po for po in purchase_orders
        if isinstance(po, dict)
    ]

    dashboard_tab, orders_tab, create_tab = st.tabs(
        [
            "Dashboard",
            "All purchase orders",
            "Create purchase order",
        ]
    )

    with dashboard_tab:
        render_po_dashboard(purchase_orders)

    with orders_tab:
        render_po_list(purchase_orders)

    with create_tab:
        render_create_po(reference)