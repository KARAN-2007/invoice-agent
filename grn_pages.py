"""Goods receipt management pages for the procurement demo.

The current version reads dataset/reference_data.json and keeps new GRNs in
preview mode until the PostgreSQL PO/GRN backend is available.
"""

import json
import re
from datetime import date
from pathlib import Path

import streamlit as st


BASE_DIR = Path(__file__).resolve().parent
REFERENCE_FILE = BASE_DIR / "dataset" / "reference_data.json"


# ==================================================
# DATA HELPERS
# ==================================================

def load_procurement_data():
    """Load procurement reference data without modifying the source file."""
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


def safe_records(data, key):
    """Return only dictionary records from a JSON list."""
    records = data.get(key, [])
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def to_number(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def normalise_description(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def get_po_number(po):
    return str(po.get("po_number") or "Not assigned")


def get_po_vendor(po):
    return str(po.get("vendor_name") or po.get("vendor") or "Unknown vendor")


def get_po_status(po):
    return str(po.get("status") or "UNKNOWN").upper()


def get_po_items(po):
    items = po.get("items") or []
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def get_grn_number(grn):
    return str(grn.get("grn_number") or "Not assigned")


def get_grn_status(grn):
    return str(grn.get("status") or "UNKNOWN").upper()


def get_grn_items(grn):
    items = grn.get("items") or []
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def format_quantity(value):
    number = to_number(value)
    return (
        f"{number:,.2f}".rstrip("0").rstrip(".")
        if number % 1
        else f"{number:,.0f}"
    )


def get_dataframe_height(row_count, max_height=900):
    return min(max_height, max(200, 38 * (row_count + 1)))


def render_status(status):
    status = str(status or "UNKNOWN").upper()

    if status in ("ACCEPTED", "APPROVED", "FULLY ACCEPTED"):
        st.success(status)
    elif status in (
        "PENDING",
        "PENDING INSPECTION",
        "PARTIALLY RECEIVED",
        "PARTIALLY ACCEPTED",
    ):
        st.warning(status)
    elif status in ("REJECTED", "CANCELLED", "VOID"):
        st.error(status)
    elif status in ("NOT RECEIVED", "NO GRN"):
        st.info(status)
    else:
        st.caption(status)


def get_po_lookup(purchase_orders):
    return {
        get_po_number(po): po
        for po in purchase_orders
    }


def get_grns_for_po(po_number, goods_receipts):
    return [
        grn
        for grn in goods_receipts
        if str(grn.get("po_number") or "") == str(po_number)
    ]


def get_grn_count_by_po(goods_receipts):
    counts = {}

    for grn in goods_receipts:
        po_number = str(grn.get("po_number") or "")

        if po_number:
            counts[po_number] = counts.get(po_number, 0) + 1

    return counts


def get_receipt_totals(po, goods_receipts):
    """Aggregate receipt quantities across every GRN linked to a PO."""

    totals_by_description = {}
    po_number = get_po_number(po)

    for grn in get_grns_for_po(po_number, goods_receipts):

        if get_grn_status(grn) in ("CANCELLED", "VOID"):
            continue

        for grn_item in get_grn_items(grn):

            description = grn_item.get("description") or ""
            key = normalise_description(description)

            if not key:
                continue

            received = to_number(
                grn_item.get("quantity_received")
            )

            accepted = to_number(
                grn_item.get("quantity_accepted")
            )

            rejected_raw = grn_item.get("quantity_rejected")

            if rejected_raw is None:
                rejected = max(received - accepted, 0.0)
            else:
                rejected = to_number(rejected_raw)

            bucket = totals_by_description.setdefault(
                key,
                {
                    "received": 0.0,
                    "accepted": 0.0,
                    "rejected": 0.0,
                },
            )

            bucket["received"] += received
            bucket["accepted"] += accepted
            bucket["rejected"] += rejected

    result = []

    for po_item in get_po_items(po):

        description = (
            po_item.get("description")
            or "Unnamed item"
        )

        key = normalise_description(description)

        values = totals_by_description.get(
            key,
            {
                "received": 0.0,
                "accepted": 0.0,
                "rejected": 0.0,
            },
        )

        ordered = to_number(
            po_item.get("quantity_ordered")
        )

        received = values["received"]
        accepted = values["accepted"]
        rejected = values["rejected"]

        result.append(
            {
                "description": description,
                "unit": po_item.get("unit") or "unit(s)",
                "ordered": ordered,
                "received": received,
                "accepted": accepted,
                "rejected": rejected,
                "remaining_to_receive": max(
                    ordered - received,
                    0.0,
                ),
                "outstanding_acceptance": max(
                    ordered - accepted,
                    0.0,
                ),
                "over_received": max(
                    received - ordered,
                    0.0,
                ),
                "over_accepted": max(
                    accepted - ordered,
                    0.0,
                ),
            }
        )

    return result


def get_po_delivery_status(po, goods_receipts):
    """Summarise goods delivery status using accepted quantities."""

    po_status = get_po_status(po)

    if po_status == "CANCELLED":
        return "CANCELLED"

    if str(
        po.get("procurement_type") or ""
    ).upper() == "SERVICE":
        return "SERVICE ORDER"

    line_totals = get_receipt_totals(
        po,
        goods_receipts,
    )

    if not line_totals:
        return "NO LINE ITEMS"

    if all(
        line["accepted"] >= line["ordered"]
        for line in line_totals
    ):
        return "FULLY ACCEPTED"

    if any(
        line["received"] > 0
        for line in line_totals
    ):
        return "PARTIALLY RECEIVED"

    return "NOT RECEIVED"


def get_return_records(po_number, goods_returns):
    return [
        record
        for record in goods_returns
        if str(record.get("po_number") or "")
        == str(po_number)
    ]


def get_service_confirmation_for_po(
    po_number,
    service_confirmations,
):
    return [
        record
        for record in service_confirmations
        if str(record.get("po_number") or "")
        == str(po_number)
    ]


# ==================================================
# GRN DETAILS
# ==================================================

def render_grn_details(
    grn,
    po_lookup,
    goods_returns,
):
    grn_number = get_grn_number(grn)

    po_number = str(
        grn.get("po_number") or "Not linked"
    )

    po = po_lookup.get(po_number)

    items = get_grn_items(grn)

    st.markdown("### Goods receipt details")

    col1, col2, col3 = st.columns(3)

    col1.metric("GRN Number", grn_number)
    col2.metric("Purchase order", po_number)
    col3.metric("Line items", len(items))

    info1, info2, info3 = st.columns(3)

    info1.write("**Received date**")
    info1.write(
        str(grn.get("received_date") or "-")
    )

    info2.write("**Vendor**")
    info2.write(
        get_po_vendor(po)
        if po
        else "Not found"
    )

    info3.write("**GRN status**")
    render_status(get_grn_status(grn))

    if po:
        st.write(
            f"**Vendor ID:** "
            f"{po.get('vendor_id') or '-'}"
        )

    st.divider()

    st.markdown("### Received items")

    if not items:
        st.info(
            "No line items are recorded for this GRN."
        )

    else:
        rows = []

        for item in items:

            received = to_number(
                item.get("quantity_received")
            )

            accepted = to_number(
                item.get("quantity_accepted")
            )

            rejected = (
                to_number(
                    item.get("quantity_rejected")
                )
                if item.get("quantity_rejected")
                is not None
                else max(
                    received - accepted,
                    0.0,
                )
            )

            pending_inspection = max(
                received - accepted - rejected,
                0.0,
            )

            rows.append(
                {
                    "Description": (
                        item.get("description") or "-"
                    ),
                    "Quantity received": (
                        format_quantity(received)
                    ),
                    "Quantity accepted": (
                        format_quantity(accepted)
                    ),
                    "Quantity rejected": (
                        format_quantity(rejected)
                    ),
                    "Pending inspection": (
                        format_quantity(
                            pending_inspection
                        )
                    ),
                }
            )

        st.dataframe(
            rows,
            use_container_width=True,
            hide_index=True,
            height=get_dataframe_height(len(rows)),
        )

    related_returns = get_return_records(
        po_number,
        goods_returns,
    )

    if related_returns:

        st.divider()

        st.markdown(
            "### Goods returns linked to this PO"
        )

        return_rows = []

        for record in related_returns:

            return_rows.append(
                {
                    "Return ID": (
                        record.get("return_id") or "-"
                    ),
                    "Original invoice": (
                        record.get(
                            "original_invoice_number"
                        )
                        or "-"
                    ),
                    "Return date": (
                        record.get("return_date") or "-"
                    ),
                    "Status": (
                        record.get("status") or "-"
                    ),
                    "Items": len(
                        record.get("items") or []
                    ),
                }
            )

        st.dataframe(
            return_rows,
            use_container_width=True,
            hide_index=True,
            height=get_dataframe_height(
                len(return_rows)
            ),
        )


# ==================================================
# DASHBOARD
# ==================================================

def render_grn_dashboard(
    purchase_orders,
    goods_receipts,
    service_confirmations,
):

    st.markdown("### Goods receipt overview")

    counts_by_po = get_grn_count_by_po(
        goods_receipts
    )

    goods_pos = [
        po
        for po in purchase_orders
        if get_po_status(po) == "APPROVED"
        and str(
            po.get("procurement_type") or ""
        ).upper() != "SERVICE"
        and get_po_items(po)
    ]

    pos_with_receipts = sum(
        1
        for po in goods_pos
        if counts_by_po.get(
            get_po_number(po),
            0,
        )
        > 0
    )

    fully_accepted = sum(
        1
        for po in goods_pos
        if get_po_delivery_status(
            po,
            goods_receipts,
        )
        == "FULLY ACCEPTED"
    )

    open_deliveries = sum(
        1
        for po in goods_pos
        if get_po_delivery_status(
            po,
            goods_receipts,
        )
        not in (
            "FULLY ACCEPTED",
            "CANCELLED",
        )
    )

    col1, col2, col3, col4 = st.columns(4)

    col1.metric(
        "Total GRNs",
        len(goods_receipts),
    )

    col2.metric(
        "POs with receipts",
        pos_with_receipts,
    )

    col3.metric(
        "Fully accepted POs",
        fully_accepted,
    )

    col4.metric(
        "Open deliveries",
        open_deliveries,
    )

    st.divider()

    st.markdown(
        "### Purchase order receipt status"
    )

    summary_rows = []

    for po in purchase_orders:

        po_number = get_po_number(po)

        is_service = (
            str(
                po.get("procurement_type") or ""
            ).upper()
            == "SERVICE"
        )

        if is_service:

            service_records = (
                get_service_confirmation_for_po(
                    po_number,
                    service_confirmations,
                )
            )

            delivery_status = (
                "SERVICE CONFIRMED"
                if service_records
                else "SERVICE CONFIRMATION PENDING"
            )

        else:
            delivery_status = get_po_delivery_status(
                po,
                goods_receipts,
            )

        line_totals = get_receipt_totals(
            po,
            goods_receipts,
        )

        accepted_lines = sum(
            1
            for line in line_totals
            if line["accepted"] >= line["ordered"]
        )

        summary_rows.append(
            {
                "PO Number": po_number,
                "Vendor": get_po_vendor(po),
                "PO Status": get_po_status(po),
                "GRNs": counts_by_po.get(
                    po_number,
                    0,
                ),
                "Lines fully accepted": (
                    f"{accepted_lines}/"
                    f"{len(line_totals)}"
                    if line_totals
                    else "-"
                ),
                "Delivery status": delivery_status,
            }
        )

    st.dataframe(
        summary_rows,
        use_container_width=True,
        hide_index=True,
        height=get_dataframe_height(
            len(summary_rows)
        ),
    )


# ==================================================
# ALL GRNs
# ==================================================

def render_grn_list(
    goods_receipts,
    po_lookup,
    goods_returns,
):

    st.markdown("### All goods receipt notes")

    search = st.text_input(
        "Search GRNs",
        placeholder=(
            "Search GRN number, "
            "PO number or vendor..."
        ),
        key="grn_search",
    ).strip().lower()

    statuses = sorted(
        {
            get_grn_status(grn)
            for grn in goods_receipts
        }
    )

    status_options = (
        ["All statuses"] + statuses
    )

    selected_status = st.selectbox(
        "Filter by status",
        status_options,
        key="grn_status_filter",
    )

    filtered = []

    for grn in goods_receipts:

        po_number = str(
            grn.get("po_number") or ""
        )

        po = po_lookup.get(po_number)

        vendor = (
            get_po_vendor(po)
            if po
            else "Unknown vendor"
        )

        searchable = " ".join(
            [
                get_grn_number(grn),
                po_number,
                vendor,
            ]
        ).lower()

        if search and search not in searchable:
            continue

        if (
            selected_status != "All statuses"
            and get_grn_status(grn)
            != selected_status
        ):
            continue

        filtered.append(grn)

    st.caption(
        f"{len(filtered)} goods receipt notes found."
    )

    if not filtered:
        st.info(
            "No GRNs match your search or filters."
        )
        return

    rows = []

    for grn in filtered:

        po_number = str(
            grn.get("po_number") or ""
        )

        po = po_lookup.get(po_number)

        rows.append(
            {
                "GRN Number": get_grn_number(grn),
                "PO Number": po_number or "-",
                "Vendor": (
                    get_po_vendor(po)
                    if po
                    else "Unknown vendor"
                ),
                "Received date": (
                    grn.get("received_date") or "-"
                ),
                "Status": get_grn_status(grn),
                "Line items": len(
                    get_grn_items(grn)
                ),
            }
        )

    st.dataframe(
        rows,
        use_container_width=True,
        hide_index=True,
        height=get_dataframe_height(len(rows)),
    )

    st.divider()

    grn_numbers = [
        get_grn_number(grn)
        for grn in filtered
    ]

    selected_grn_number = st.selectbox(
        "Select a GRN to inspect",
        grn_numbers,
        key="grn_detail_selection",
    )

    selected_grn = next(
        grn
        for grn in filtered
        if get_grn_number(grn)
        == selected_grn_number
    )

    render_grn_details(
        selected_grn,
        po_lookup,
        goods_returns,
    )


# ==================================================
# RECORD GOODS RECEIPT - PREVIEW
# ==================================================

def render_record_grn_preview(
    purchase_orders,
    goods_receipts,
):

    st.markdown("### Record a goods receipt")

    st.info(
        "Preview mode: PostgreSQL GRN create "
        "functions are not connected yet. "
        "You can prepare a receipt and inspect "
        "its calculations, but it will not be saved."
    )

    eligible_pos = [
        po
        for po in purchase_orders
        if get_po_status(po) == "APPROVED"
        and str(
            po.get("procurement_type") or ""
        ).upper() != "SERVICE"
        and get_po_items(po)
    ]

    if not eligible_pos:
        st.warning(
            "There are no approved goods purchase "
            "orders available for receipt."
        )
        return

    po_lookup = get_po_lookup(eligible_pos)

    po_numbers = [
        get_po_number(po)
        for po in eligible_pos
    ]

    selected_po_number = st.selectbox(
        "Select purchase order",
        po_numbers,
        key="grn_create_po_select",
    )

    selected_po = po_lookup[
        selected_po_number
    ]

    vendor_name = get_po_vendor(
        selected_po
    )

    line_totals = get_receipt_totals(
        selected_po,
        goods_receipts,
    )

    st.markdown(
        "#### Purchase order information"
    )

    c1, c2, c3 = st.columns(3)

    c1.metric(
        "PO Number",
        selected_po_number,
    )

    c2.metric(
        "Vendor",
        vendor_name,
    )

    c3.metric(
        "Line items",
        len(line_totals),
    )

    if not line_totals:
        st.info(
            "This purchase order has no goods "
            "line items."
        )
        return

    with st.form(
        f"create_grn_preview_form_"
        f"{selected_po_number}"
    ):

        st.markdown(
            "#### Delivery information"
        )

        received_date = st.date_input(
            "Received date",
            value=date.today(),
            key=(
                f"grn_received_date_"
                f"{selected_po_number}"
            ),
        )

        delivery_reference = st.text_input(
            "Delivery challan / reference "
            "number (optional)",
            key=(
                f"grn_delivery_reference_"
                f"{selected_po_number}"
            ),
        )

        notes = st.text_area(
            "Receipt notes (optional)",
            key=(
                f"grn_notes_"
                f"{selected_po_number}"
            ),
        )

        st.divider()

        st.markdown(
            "#### Quantities received in this delivery"
        )

        draft_items = []

        for index, line in enumerate(
            line_totals
        ):

            description = line["description"]

            remaining = (
                line["remaining_to_receive"]
            )

            # Convert to integer for whole-number
            # quantity input fields.
            remaining_int = max(
                int(remaining),
                0,
            )

            st.markdown(
                f"**{index + 1}. "
                f"{description}**"
            )

            st.caption(
                f"Ordered: "
                f"{format_quantity(line['ordered'])} "
                f"{line['unit']} · "
                f"Previously received: "
                f"{format_quantity(line['received'])} · "
                f"Previously accepted: "
                f"{format_quantity(line['accepted'])} · "
                f"Remaining to receive: "
                f"{format_quantity(remaining)}"
            )

            col1, col2, col3 = st.columns(3)

            # FIX:
            # All three quantity fields use integers,
            # so Streamlit displays 0 instead of 0.00.

            with col1:

                quantity_received = st.number_input(
                    "Received in this delivery",
                    min_value=0,
                    max_value=remaining_int,
                    value=0,
                    step=1,
                    key=(
                        f"grn_received_"
                        f"{selected_po_number}_"
                        f"{index}"
                    ),
                )

            with col2:

                quantity_accepted = st.number_input(
                    "Accepted",
                    min_value=0,
                    max_value=remaining_int,
                    value=0,
                    step=1,
                    key=(
                        f"grn_accepted_"
                        f"{selected_po_number}_"
                        f"{index}"
                    ),
                )

            with col3:

                quantity_rejected = st.number_input(
                    "Rejected",
                    min_value=0,
                    max_value=remaining_int,
                    value=0,
                    step=1,
                    key=(
                        f"grn_rejected_"
                        f"{selected_po_number}_"
                        f"{index}"
                    ),
                )

            draft_items.append(
                {
                    "description": description,
                    "unit": line["unit"],
                    "quantity_ordered": (
                        line["ordered"]
                    ),
                    "previously_received": (
                        line["received"]
                    ),
                    "previously_accepted": (
                        line["accepted"]
                    ),
                    "quantity_received": (
                        quantity_received
                    ),
                    "quantity_accepted": (
                        quantity_accepted
                    ),
                    "quantity_rejected": (
                        quantity_rejected
                    ),
                }
            )

            st.divider()

        submitted = st.form_submit_button(
            "Generate GRN preview",
            type="primary",
            use_container_width=True,
        )

    if submitted:

        errors = []

        total_received = 0.0
        total_accepted = 0.0
        total_rejected = 0.0
        total_pending_inspection = 0.0

        for item in draft_items:

            received = item[
                "quantity_received"
            ]

            accepted = item[
                "quantity_accepted"
            ]

            rejected = item[
                "quantity_rejected"
            ]

            remaining = max(
                item["quantity_ordered"]
                - item["previously_received"],
                0.0,
            )

            if received > remaining + 0.0001:

                errors.append(
                    f"{item['description']}: "
                    "received quantity exceeds the "
                    "remaining PO quantity."
                )

            if accepted + rejected > received + 0.0001:

                errors.append(
                    f"{item['description']}: "
                    "accepted plus rejected cannot "
                    "exceed received quantity."
                )

            total_received += received
            total_accepted += accepted
            total_rejected += rejected

            total_pending_inspection += max(
                received - accepted - rejected,
                0.0,
            )

        if total_received <= 0:

            errors.append(
                "Enter a positive received quantity "
                "for at least one item."
            )

        if errors:

            for error in errors:
                st.error(error)

        else:

            st.session_state[
                "grn_draft_preview"
            ] = {
                "po_number": (
                    selected_po_number
                ),
                "vendor": vendor_name,
                "received_date": (
                    received_date.isoformat()
                ),
                "delivery_reference": (
                    delivery_reference.strip()
                ),
                "notes": notes.strip(),
                "items": [
                    item
                    for item in draft_items
                    if item[
                        "quantity_received"
                    ] > 0
                ],
                "total_received": (
                    total_received
                ),
                "total_accepted": (
                    total_accepted
                ),
                "total_rejected": (
                    total_rejected
                ),
                "total_pending_inspection": (
                    total_pending_inspection
                ),
            }

    draft = st.session_state.get(
        "grn_draft_preview"
    )

    if (
        draft
        and draft.get("po_number")
        == selected_po_number
    ):

        st.divider()

        st.markdown(
            "### Goods receipt preview"
        )

        st.warning(
            "Temporary preview only. No GRN number "
            "has been assigned and no database "
            "record has been created."
        )

        m1, m2, m3, m4 = st.columns(4)

        m1.metric(
            "Received",
            format_quantity(
                draft["total_received"]
            ),
        )

        m2.metric(
            "Accepted",
            format_quantity(
                draft["total_accepted"]
            ),
        )

        m3.metric(
            "Rejected",
            format_quantity(
                draft["total_rejected"]
            ),
        )

        m4.metric(
            "Pending inspection",
            format_quantity(
                draft[
                    "total_pending_inspection"
                ]
            ),
        )

        st.write(
            f"**PO:** "
            f"{draft['po_number']}"
        )

        st.write(
            f"**Vendor:** "
            f"{draft['vendor']}"
        )

        st.write(
            f"**Received date:** "
            f"{draft['received_date']}"
        )

        st.write(
            f"**Delivery reference:** "
            f"{draft['delivery_reference'] or '-'}"
        )

        st.write(
            f"**Notes:** "
            f"{draft['notes'] or '-'}"
        )

        preview_rows = []

        for item in draft["items"]:

            preview_rows.append(
                {
                    "Description": (
                        item["description"]
                    ),
                    "Received": (
                        format_quantity(
                            item[
                                "quantity_received"
                            ]
                        )
                    ),
                    "Accepted": (
                        format_quantity(
                            item[
                                "quantity_accepted"
                            ]
                        )
                    ),
                    "Rejected": (
                        format_quantity(
                            item[
                                "quantity_rejected"
                            ]
                        )
                    ),
                    "Pending inspection": (
                        format_quantity(
                            item[
                                "quantity_received"
                            ]
                            - item[
                                "quantity_accepted"
                            ]
                            - item[
                                "quantity_rejected"
                            ]
                        )
                    ),
                }
            )

        st.dataframe(
            preview_rows,
            use_container_width=True,
            hide_index=True,
            height=get_dataframe_height(
                len(preview_rows)
            ),
        )

        if st.button(
            "Clear GRN preview",
            key="clear_grn_preview",
        ):
            st.session_state.pop(
                "grn_draft_preview",
                None,
            )
            st.rerun()


# ==================================================
# SERVICE CONFIRMATIONS
# ==================================================

def render_service_confirmations(
    service_confirmations,
    po_lookup,
):

    st.markdown(
        "### Service confirmations"
    )

    st.caption(
        "Service purchase orders are confirmed "
        "through service records rather than "
        "physical goods receipt notes."
    )

    if not service_confirmations:

        st.info(
            "No service confirmations are available."
        )
        return

    rows = []

    for record in service_confirmations:

        po_number = str(
            record.get("po_number") or ""
        )

        po = po_lookup.get(po_number)

        rows.append(
            {
                "Confirmation ID": (
                    record.get(
                        "confirmation_id"
                    )
                    or "-"
                ),
                "PO Number": (
                    po_number or "-"
                ),
                "Vendor": (
                    get_po_vendor(po)
                    if po
                    else "Unknown vendor"
                ),
                "Service": (
                    record.get("service_name")
                    or "-"
                ),
                "Period from": (
                    record.get("period_from")
                    or "-"
                ),
                "Period to": (
                    record.get("period_to")
                    or "-"
                ),
                "Hours accepted": (
                    record.get(
                        "hours_accepted",
                        "-",
                    )
                ),
                "Status": (
                    record.get("status")
                    or "-"
                ),
            }
        )

    st.dataframe(
        rows,
        use_container_width=True,
        hide_index=True,
        height=get_dataframe_height(len(rows)),
    )

    for record in service_confirmations:

        with st.expander(
            str(
                record.get(
                    "confirmation_id"
                )
                or "Service confirmation"
            )
        ):

            st.write(
                f"**PO:** "
                f"{record.get('po_number') or '-'}"
            )

            st.write(
                f"**Service:** "
                f"{record.get('service_name') or '-'}"
            )

            st.write(
                f"**Service period:** "
                f"{record.get('period_from') or '-'} "
                f"to "
                f"{record.get('period_to') or '-'}"
            )

            st.write(
                f"**Hours accepted:** "
                f"{record.get('hours_accepted', '-')}"
            )

            st.write(
                f"**Approved by:** "
                f"{record.get('approved_by') or '-'}"
            )

            render_status(
                record.get("status")
            )


# ==================================================
# GOODS RETURNS
# ==================================================

def render_goods_returns(
    goods_returns,
    po_lookup,
):

    st.markdown("### Goods returns")

    st.caption(
        "Returns are listed separately from GRNs. "
        "They are not automatically deducted from "
        "accepted receipt quantities in this preview."
    )

    if not goods_returns:

        st.info(
            "No goods returns are available."
        )
        return

    rows = []

    for record in goods_returns:

        po_number = str(
            record.get("po_number") or ""
        )

        po = po_lookup.get(po_number)

        rows.append(
            {
                "Return ID": (
                    record.get("return_id")
                    or "-"
                ),
                "Original invoice": (
                    record.get(
                        "original_invoice_number"
                    )
                    or "-"
                ),
                "PO Number": (
                    po_number or "-"
                ),
                "Vendor": (
                    get_po_vendor(po)
                    if po
                    else "Unknown vendor"
                ),
                "Return date": (
                    record.get("return_date")
                    or "-"
                ),
                "Status": (
                    record.get("status")
                    or "-"
                ),
            }
        )

    st.dataframe(
        rows,
        use_container_width=True,
        hide_index=True,
        height=get_dataframe_height(len(rows)),
    )

    for record in goods_returns:

        with st.expander(
            str(
                record.get("return_id")
                or "Goods return"
            )
        ):

            st.write(
                f"**PO:** "
                f"{record.get('po_number') or '-'}"
            )

            st.write(
                f"**Original invoice:** "
                f"{record.get('original_invoice_number') or '-'}"
            )

            st.write(
                f"**Vendor ID:** "
                f"{record.get('vendor_id') or '-'}"
            )

            st.write(
                f"**Return date:** "
                f"{record.get('return_date') or '-'}"
            )

            render_status(
                record.get("status")
            )

            item_rows = []

            for item in record.get("items") or []:

                if not isinstance(item, dict):
                    continue

                item_rows.append(
                    {
                        "Description": (
                            item.get("description")
                            or "-"
                        ),
                        "Quantity returned": (
                            item.get(
                                "quantity_returned",
                                "-",
                            )
                        ),
                        "Quantity accepted": (
                            item.get(
                                "quantity_accepted",
                                "-",
                            )
                        ),
                        "Reason": (
                            item.get("reason")
                            or "-"
                        ),
                    }
                )

            if item_rows:

                st.dataframe(
                    item_rows,
                    use_container_width=True,
                    hide_index=True,
                    height=get_dataframe_height(
                        len(item_rows)
                    ),
                )


# ==================================================
# MAIN GRN PAGE
# ==================================================

def render_grn_page(user):
    """Render the goods receipt management interface."""

    role = str(
        user.get("role") or ""
    ).strip().lower()

    if role != "procurement_officer":

        st.error(
            "You do not have permission to manage "
            "goods receipts."
        )

        st.caption(
            "Detected role: "
            + repr(role)
        )

        return

    st.title("Goods receipt management")

    st.caption(
        "Record deliveries, inspect received "
        "quantities and track outstanding items."
    )

    st.info(
        "Procurement data is currently displayed "
        "in read-only preview mode. New GRNs are "
        "previews only until PostgreSQL integration "
        "is connected."
    )

    reference = load_procurement_data()

    purchase_orders = safe_records(
        reference,
        "purchase_orders",
    )

    goods_receipts = safe_records(
        reference,
        "goods_receipts",
    )

    service_confirmations = safe_records(
        reference,
        "service_confirmations",
    )

    goods_returns = safe_records(
        reference,
        "goods_returns",
    )

    po_lookup = get_po_lookup(
        purchase_orders
    )

    (
        dashboard_tab,
        grns_tab,
        record_tab,
        services_tab,
        returns_tab,
    ) = st.tabs(
        [
            "Dashboard",
            "All GRNs",
            "Record goods receipt",
            "Service confirmations",
            "Goods returns",
        ]
    )

    with dashboard_tab:

        render_grn_dashboard(
            purchase_orders,
            goods_receipts,
            service_confirmations,
        )

    with grns_tab:

        render_grn_list(
            goods_receipts,
            po_lookup,
            goods_returns,
        )

    with record_tab:

        render_record_grn_preview(
            purchase_orders,
            goods_receipts,
        )

    with services_tab:

        render_service_confirmations(
            service_confirmations,
            po_lookup,
        )

    with returns_tab:

        render_goods_returns(
            goods_returns,
            po_lookup,
        )