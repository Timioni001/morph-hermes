"""Warehouse — an independent inventory and shipment application.

Third separate process, third separate database, third separate token.
"""
from __future__ import annotations

from apps._runtime import AppSpec, EntitySpec, FieldSpec, OperationSpec, build_app

_CATEGORY = ("bags", "peripherals", "displays", "cables")
_SHIP_STATUS = ("preparing", "in_transit", "delayed", "delivered", "exception")
_SERVICE = ("standard", "priority")

STOCK = EntitySpec(
    name="stock_items",
    label="Stock items",
    description="Everything held in the warehouse with its availability and reorder policy.",
    primary_key="sku",
    title_field="product_name",
    fields=(
        FieldSpec("sku", "id", "SKU", role="key"),
        FieldSpec("product_name", "string", "Product", role="title"),
        FieldSpec("category", "enum", "Category", values=_CATEGORY, role="status"),
        FieldSpec("on_hand", "int", "On hand", role="metric"),
        FieldSpec("reserved", "int", "Reserved", role="metric"),
        FieldSpec("available", "int", "Available", role="metric"),
        FieldSpec("reorder_level", "int", "Reorder level", role="metric"),
        FieldSpec("unit_cost", "money", "Unit cost", role="amount", unit="USD"),
        FieldSpec("supplier", "string", "Supplier"),
        FieldSpec("lead_time_days", "int", "Lead time", role="metric", unit="d"),
        FieldSpec("location", "string", "Bin"),
        FieldSpec("restock_requested", "bool", "Restock requested", role="flag", writable=True),
        FieldSpec("restock_quantity", "int", "Restock qty", role="metric", writable=True),
        FieldSpec("restock_note", "text", "Restock note", writable=True),
    ),
    operations=(
        OperationSpec(
            id="request_restock", label="Request restock",
            description="Raise a restock request with the supplier for this SKU.",
            effect={"restock_requested": True, "restock_quantity": "{quantity}", "restock_note": "{note}"},
            params=(
                FieldSpec("quantity", "int", "Quantity", required=True, minimum=1, maximum=5000),
                FieldSpec("note", "text", "Note", max_length=200),
            ),
            precondition=({"field": "restock_requested", "op": "eq", "value": False,
                           "message": "a restock request is already open for this SKU"},),
            risk="medium",
        ),
        OperationSpec(
            id="cancel_restock", label="Cancel restock request",
            description="Withdraw an open restock request.",
            effect={"restock_requested": False, "restock_quantity": 0},
            precondition=({"field": "restock_requested", "op": "eq", "value": True,
                           "message": "no restock request is open for this SKU"},),
            risk="low",
        ),
    ),
    seed=(
        {"sku": "SKU-BACKPACK-01", "product_name": "Classic Backpack", "category": "bags",
         "on_hand": 7, "reserved": 3, "available": 4, "reorder_level": 10, "unit_cost": 42.00,
         "supplier": "Karoo Goods", "lead_time_days": 9, "location": "A-14",
         "restock_requested": False, "restock_quantity": 0, "restock_note": None},
        {"sku": "SKU-MOUSE-02", "product_name": "Wireless Mouse", "category": "peripherals",
         "on_hand": 29, "reserved": 5, "available": 24, "reorder_level": 8, "unit_cost": 11.50,
         "supplier": "Peripheral Works", "lead_time_days": 4, "location": "B-02",
         "restock_requested": False, "restock_quantity": 0, "restock_note": None},
        {"sku": "SKU-HUB-03", "product_name": "USB-C Hub", "category": "peripherals",
         "on_hand": 9, "reserved": 7, "available": 2, "reorder_level": 12, "unit_cost": 23.75,
         "supplier": "Peripheral Works", "lead_time_days": 4, "location": "B-07",
         "restock_requested": False, "restock_quantity": 0, "restock_note": None},
        {"sku": "SKU-CABLE-05", "product_name": "USB-C Cable 2m", "category": "cables",
         "on_hand": 140, "reserved": 12, "available": 128, "reorder_level": 40, "unit_cost": 3.20,
         "supplier": "Wirehouse", "lead_time_days": 2, "location": "C-01",
         "restock_requested": False, "restock_quantity": 0, "restock_note": None},
        {"sku": "SKU-MONITOR-07", "product_name": "27\" 4K Monitor", "category": "displays",
         "on_hand": 4, "reserved": 3, "available": 1, "reorder_level": 6, "unit_cost": 318.00,
         "supplier": "Clearview Displays", "lead_time_days": 18, "location": "D-11",
         "restock_requested": False, "restock_quantity": 0, "restock_note": None},
        {"sku": "SKU-STAND-08", "product_name": "Monitor Stand", "category": "displays",
         "on_hand": 22, "reserved": 2, "available": 20, "reorder_level": 6, "unit_cost": 54.00,
         "supplier": "Clearview Displays", "lead_time_days": 18, "location": "D-12",
         "restock_requested": True, "restock_quantity": 15, "restock_note": "Seasonal uplift."},
        {"sku": "SKU-POUCH-09", "product_name": "Travel Pouch", "category": "bags",
         "on_hand": 3, "reserved": 2, "available": 1, "reorder_level": 15, "unit_cost": 8.90,
         "supplier": "Karoo Goods", "lead_time_days": 9, "location": "A-15",
         "restock_requested": False, "restock_quantity": 0, "restock_note": None},
    ),
)

SHIPMENTS = EntitySpec(
    name="shipments",
    label="Shipments",
    description="Outbound shipments, their carrier and current transit state.",
    title_field="order_ref",
    fields=(
        FieldSpec("id", "id", "Shipment", role="key"),
        FieldSpec("order_ref", "string", "Order reference", role="title"),
        FieldSpec("sku", "string", "SKU"),
        FieldSpec("carrier", "enum", "Carrier", values=("Northwind", "Vector Freight"), role="status"),
        FieldSpec("status", "enum", "Status", values=_SHIP_STATUS, role="status"),
        FieldSpec("service_level", "enum", "Service", values=_SERVICE, role="status", writable=True),
        FieldSpec("dispatched_at", "datetime", "Dispatched", role="timestamp"),
        FieldSpec("eta", "datetime", "ETA", role="timestamp"),
        FieldSpec("days_in_transit", "int", "In transit", role="metric", unit="d"),
        FieldSpec("expedited", "bool", "Expedited", role="flag", writable=True),
        FieldSpec("expedite_reason", "text", "Expedite reason", writable=True),
    ),
    operations=(
        OperationSpec(
            id="expedite_shipment", label="Expedite shipment",
            description="Upgrade the shipment to priority service with the carrier.",
            effect={"expedited": True, "service_level": "priority", "expedite_reason": "{reason}"},
            params=(FieldSpec("reason", "text", "Reason", required=True, max_length=200),),
            precondition=(
                {"field": "expedited", "op": "eq", "value": False, "message": "shipment is already expedited"},
                {"field": "status", "op": "nin", "value": ["delivered"], "message": "shipment already delivered"},
            ),
            risk="medium",
        ),
    ),
    seed=(
        {"id": "SHP-901", "order_ref": "ORD-2041", "sku": "SKU-BACKPACK-01", "carrier": "Northwind",
         "status": "delayed", "service_level": "standard", "dispatched_at": "2026-11-01T06:00:00Z",
         "eta": "2026-11-10T17:00:00Z", "days_in_transit": 7, "expedited": False, "expedite_reason": None},
        {"id": "SHP-902", "order_ref": "ORD-2043", "sku": "SKU-HUB-03", "carrier": "Vector Freight",
         "status": "delayed", "service_level": "standard", "dispatched_at": "2026-11-02T06:00:00Z",
         "eta": "2026-11-09T17:00:00Z", "days_in_transit": 6, "expedited": False, "expedite_reason": None},
        {"id": "SHP-903", "order_ref": "ORD-2045", "sku": "SKU-HUB-03", "carrier": "Vector Freight",
         "status": "exception", "service_level": "standard", "dispatched_at": "2026-10-31T06:00:00Z",
         "eta": "2026-11-11T17:00:00Z", "days_in_transit": 8, "expedited": False, "expedite_reason": None},
        {"id": "SHP-904", "order_ref": "ORD-2044", "sku": "SKU-CABLE-05", "carrier": "Northwind",
         "status": "delivered", "service_level": "standard", "dispatched_at": "2026-11-05T06:00:00Z",
         "eta": "2026-11-07T17:00:00Z", "days_in_transit": 2, "expedited": False, "expedite_reason": None},
        {"id": "SHP-905", "order_ref": "ORD-2046", "sku": "SKU-MOUSE-02", "carrier": "Northwind",
         "status": "in_transit", "service_level": "standard", "dispatched_at": "2026-11-06T06:00:00Z",
         "eta": "2026-11-11T17:00:00Z", "days_in_transit": 1, "expedited": False, "expedite_reason": None},
        {"id": "SHP-906", "order_ref": "ORD-2047", "sku": "SKU-MONITOR-07", "carrier": "Vector Freight",
         "status": "delayed", "service_level": "standard", "dispatched_at": "2026-10-30T06:00:00Z",
         "eta": "2026-11-12T17:00:00Z", "days_in_transit": 9, "expedited": False, "expedite_reason": None},
        {"id": "SHP-907", "order_ref": "ORD-2050", "sku": "SKU-BACKPACK-01", "carrier": "Northwind",
         "status": "delayed", "service_level": "standard", "dispatched_at": "2026-11-03T06:00:00Z",
         "eta": "2026-11-09T17:00:00Z", "days_in_transit": 5, "expedited": False, "expedite_reason": None},
    ),
)

SPEC = AppSpec(
    id="warehouse",
    label="Warehouse",
    vendor="Pallet Systems",
    description="Inventory and outbound logistics: stock levels, reorder policy, shipments.",
    entities=(STOCK, SHIPMENTS),
    token_env="MORPH_WAREHOUSE_TOKEN",
    port=8813,
    accent="#0f766e",
)

app = build_app(SPEC)
