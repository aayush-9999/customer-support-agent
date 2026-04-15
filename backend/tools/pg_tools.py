# backend/tools/pg_tools.py
import re
import logging
from typing import Any
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
import uuid
from backend.tools.base import BaseTool
import re
import json
from datetime import datetime, date, timezone, timedelta

logger = logging.getLogger(__name__)

# ── 0. Think Tool ───────────────────────────────────────────────────────────────
class ThinkTool(BaseTool):
    @property
    def name(self) -> str:
        return "think"

    @property
    def description(self) -> str:
        return (
            "Reason through a problem before acting. Call BEFORE any data-fetching "
            "or mutation tool. No side effects — just records your reasoning."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "reasoning": {
                    "type": "string",
                    "description": (
                        "Your step-by-step plan: what the customer wants, "
                        "what data you already have, which tool you'll call next and why, "
                        "and what arguments you already have confirmed."
                    )
                }
            },
            "required": ["reasoning"]
        }

    async def execute(self, **kwargs: Any) -> dict:
        reasoning = kwargs.get("reasoning", "")
        logger.debug(f"[THINK] {reasoning[:200]}")
        return {
            "ok": True,
            "instruction": (
                "Reasoning recorded. Now act on your plan: "
                "call the required tool directly. "
                "Do NOT call think again until you have new data."
            )
        }


# ── 1. Get Order History ─────────────────────────────────────────────────────

class GetOrderHistoryPG(BaseTool):
    def __init__(self, session_factory):
        self._session_factory = session_factory

    @property
    def name(self) -> str:
        return "get_order_history"

    @property
    def description(self) -> str:
        return (
            "List all orders for a customer using their email address. "
            "Returns a summary list: order IDs, statuses, dates, and totals. "
            "Use when the customer asks 'my orders', 'order history', "
            "'how many orders do I have', 'second last order', 'previous order'. "
            "To get full item details for a specific order, follow up with get_order_details."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "Customer's email address"
                }
            },
            "required": ["email"]
        }

    async def execute(self, **kwargs: Any) -> dict:
        email = kwargs.get("email", "").strip().lower()
        if not email:
            return self.error("email is required.")

        try:
            async with self._session_factory() as session:

                user_result = await session.execute(
                    text("SELECT id FROM users WHERE LOWER(email) = :email"),
                    {"email": email}
                )
                if not user_result.mappings().first():
                    return self.error(f"No account found for email: {email}")

                customer_result = await session.execute(
                    text("SELECT customer_id FROM customers WHERE LOWER(email) = :email"),
                    {"email": email}
                )
                customer = customer_result.mappings().first()
                if not customer:
                    return self.error("No order history found for this account.")

                rows = await session.execute(
                    text("""
                        SELECT
                            o.order_id,
                            o.order_status,
                            o.order_purchase_timestamp,
                            o.order_estimated_delivery_date,
                            agg_pay.total_payment_value
                        FROM orders o
                        LEFT JOIN (
                            SELECT order_id, SUM(payment_value) AS total_payment_value
                            FROM order_payments
                            GROUP BY order_id
                        ) agg_pay ON agg_pay.order_id = o.order_id
                        WHERE o.customer_id = :customer_id
                        ORDER BY o.order_purchase_timestamp DESC
                    """),
                    {"customer_id": customer["customer_id"]}
                )
                rows = rows.mappings().all()

                if not rows:
                    return self.success({
                        "orders": [],
                        "message": "No orders found for this account."
                    })

                orders = [
                    {
                        "order_id":           row["order_id"],
                        "status":             row["order_status"],
                        "placed_at":          str(row["order_purchase_timestamp"]) if row["order_purchase_timestamp"] else None,
                        "estimated_delivery": str(row["order_estimated_delivery_date"]) if row["order_estimated_delivery_date"] else None,
                        "total_paid":         float(row["total_payment_value"]) if row["total_payment_value"] else None,
                    }
                    for row in rows
                ]

                return self.success({
                    "email":        email,
                    "total_orders": len(orders),
                    "orders":       orders,
                    "instructions":"Always use the exact order_id values as returned above when calling other tools. Never shorten or modify them.",
                })

        except Exception as e:
            logger.exception(f"get_order_history failed for {email}")
            return self.error(f"Failed to retrieve order history: {str(e)}")


# ── 2. Get Order Details ─────────────────────────────────────────────────────

class GetOrderDetailsPG(BaseTool):
    def __init__(self, session_factory):
        self._session_factory = session_factory

    @property
    def name(self) -> str:
        return "get_order_details"

    @property
    def description(self) -> str:
        return (
            "Retrieve full details of a single order: products, prices, "
            "payment method, status, and delivery dates. "
            "Pass order_id for a specific order. "
            "If no order_id is given, returns the customer's most recent order. "
            "Use when the customer asks 'what did I order', 'show my latest order', "
            "or after get_order_history to drill into a specific order."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "Customer's email address"
                },
                "order_id": {
                    "type": "string",
                    "description": "Specific order ID (optional — omit for latest order)"
                }
            },
            "required": ["email"]
        }

    async def execute(self, **kwargs: Any) -> dict:
        email    = kwargs.get("email", "").strip().lower()
        order_id = kwargs.get("order_id", "").strip() or None

        if not email:
            return self.error("email is required.")

        try:
            async with self._session_factory() as session:

                user_result = await session.execute(
                    text("SELECT id, email FROM users WHERE LOWER(email) = :email"),
                    {"email": email}
                )
                if not user_result.mappings().first():
                    return self.error(f"No account found for email: {email}")

                customer_result = await session.execute(
                    text("SELECT customer_id FROM customers WHERE LOWER(email) = :email"),
                    {"email": email}
                )
                customer = customer_result.mappings().first()
                if not customer:
                    return self.error("No order history found for this account.")

                customer_id = customer["customer_id"]

                if not order_id:
                    latest_result = await session.execute(
                        text("""
                            SELECT order_id
                            FROM orders
                            WHERE customer_id = :customer_id
                            ORDER BY order_purchase_timestamp DESC
                            LIMIT 1
                        """),
                        {"customer_id": customer_id}
                    )
                    latest = latest_result.mappings().first()
                    if not latest:
                        return self.success({
                            "orders": [],
                            "message": "No orders found for this account."
                        })
                    order_id  = latest["order_id"]
                    is_latest = True
                else:
                    is_latest = False

                rows = await session.execute(
                    text("""
                        SELECT
                            o.order_id,
                            o.order_status,
                            o.order_purchase_timestamp,
                            o.order_estimated_delivery_date,
                            o.order_delivered_customer_date,

                            oi.order_item_id,
                            oi.price,
                            oi.freight_value,

                            p.product_name,
                            p.product_category_name,

                            agg_pay.payment_types,
                            agg_pay.total_payment_value

                        FROM orders o
                        JOIN order_items oi ON oi.order_id = o.order_id
                        JOIN products    p  ON p.product_id = oi.product_id
                        LEFT JOIN (
                            SELECT
                                order_id,
                                STRING_AGG(DISTINCT payment_type, ', ') AS payment_types,
                                SUM(payment_value)                      AS total_payment_value
                            FROM order_payments
                            GROUP BY order_id
                        ) agg_pay ON agg_pay.order_id = o.order_id

                        WHERE o.customer_id = :customer_id
                          AND o.order_id    = :order_id
                        ORDER BY oi.order_item_id ASC
                    """),
                    {"customer_id": customer_id, "order_id": order_id}
                )
                rows = rows.mappings().all()

                if not rows:
                    return self.error(f"No order found with ID {order_id}.")

                first = rows[0]
                order = {
                    "email":              email,
                    "order_id":           first["order_id"],
                    "status":             first["order_status"],
                    "placed_at":          str(first["order_purchase_timestamp"]) if first["order_purchase_timestamp"] else None,
                    "estimated_delivery": str(first["order_estimated_delivery_date"]) if first["order_estimated_delivery_date"] else None,
                    "delivered_at":       str(first["order_delivered_customer_date"]) if first["order_delivered_customer_date"] else None,
                    "payment_types":      first["payment_types"],
                    "total_paid":         float(first["total_payment_value"]) if first["total_payment_value"] else None,
                    "is_latest_order":    is_latest,
                    "items":              [],
                }

                for row in rows:
                    order["items"].append({
                        "product_name": row["product_name"],
                        "category":     row["product_category_name"],
                        "price":        float(row["price"]),
                        "freight":      float(row["freight_value"]),
                        "item_total":   float(row["price"] + row["freight_value"]),
                    })

                return self.success(order)

        except Exception as e:
            logger.exception(f"get_order_details_pg failed for {email}")
            return self.error(f"Failed to retrieve order: {str(e)}")


# ── 3. Get Order Status ──────────────────────────────────────────────────────

class GetOrderStatusPG(BaseTool):
    def __init__(self, session_factory):
        self._session_factory = session_factory

    @property
    def name(self) -> str:
        return "get_order_status"

    @property
    def description(self) -> str:
        return (
            "Get the current status and tracking state of an order. "
            "Returns status, plain-language explanation, estimated delivery, "
            "and a delay flag if the order is overdue. "
            "Use when the customer asks 'where is my order', 'has it shipped', "
            "'is my order delayed', or 'when will it arrive'."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "Customer's email address"
                },
                "order_id": {
                    "type": "string",
                    "description": "Specific order ID (optional). If not provided, latest order is used."
                }
            },
            "required": ["email"]
        }

    async def execute(self, **kwargs: Any) -> dict:
        order_id = kwargs.get("order_id", "").strip() or None
        email    = kwargs.get("email", "").strip().lower()

        if not email:
            return self.error("email is required.")

        try:
            async with self._session_factory() as session:

                user_result = await session.execute(
                    text("SELECT id FROM users WHERE LOWER(email) = :email"),
                    {"email": email}
                )
                if not user_result.mappings().first():
                    return self.error(f"No account found for email: {email}")

                customer_result = await session.execute(
                    text("SELECT customer_id FROM customers WHERE LOWER(email) = :email"),
                    {"email": email}
                )
                customer = customer_result.mappings().first()
                if not customer:
                    return self.error("No order history found for this account.")

                customer_id = customer["customer_id"]

                if not order_id:
                    latest_result = await session.execute(
                        text("""
                            SELECT order_id FROM orders
                            WHERE customer_id = :customer_id
                            ORDER BY order_purchase_timestamp DESC
                            LIMIT 1
                        """),
                        {"customer_id": customer_id}
                    )
                    latest = latest_result.mappings().first()
                    if not latest:
                        return self.error("No orders found for this account.")
                    order_id = latest["order_id"]

                row_result = await session.execute(
                    text("""
                        SELECT
                            o.order_id,
                            o.order_status,
                            o.order_purchase_timestamp,
                            o.order_estimated_delivery_date,
                            o.order_delivered_customer_date,
                            CASE o.order_status
                                WHEN 'created'    THEN 'Order placed and awaiting confirmation.'
                                WHEN 'approved'   THEN 'Payment confirmed. Order queued for processing.'
                                WHEN 'invoiced'   THEN 'Invoice generated. Order is being prepared.'
                                WHEN 'processing' THEN 'Order is being packed and prepared for dispatch.'
                                WHEN 'shipped'    THEN 'Order has left the warehouse and is in transit.'
                                WHEN 'delivered'  THEN 'Order has been delivered to the shipping address.'
                                WHEN 'cancelled'  THEN 'Order has been cancelled.'
                                ELSE                   'Status temporarily unavailable. Please contact support.'
                            END AS status_description,
                            CASE
                                WHEN o.order_estimated_delivery_date IS NOT NULL
                                 AND o.order_estimated_delivery_date::date < CURRENT_DATE
                                 AND o.order_status NOT IN ('delivered', 'cancelled')
                                THEN TRUE
                                ELSE FALSE
                            END AS is_delayed
                        FROM orders o
                        WHERE o.order_id    = :order_id
                          AND o.customer_id = :customer_id
                        LIMIT 1
                    """),
                    {"order_id": order_id, "customer_id": customer_id}
                )
                row = row_result.mappings().first()

                if not row:
                    return self.error(f"No order found with ID {order_id}.")

                raw_status = row["order_status"] or "unavailable"

                result = {
                    "email":       email,
                    "order_id":    order_id,
                    "status":      raw_status,
                    "explanation": row["status_description"],
                    "ordered_at":  str(row["order_purchase_timestamp"]) if row["order_purchase_timestamp"] else None,
                    "is_delayed":  bool(row["is_delayed"]),
                }

                if raw_status not in ("delivered", "cancelled", "unavailable"):
                    eta = row["order_estimated_delivery_date"]
                    if eta:
                        result["estimated_delivery"] = str(eta)

                if raw_status == "delivered":
                    delivered = row["order_delivered_customer_date"]
                    if delivered:
                        result["delivered_at"] = str(delivered)

                return self.success(result)

        except Exception as e:
            logger.exception(f"get_order_status_pg failed for email={email}, order_id={order_id}")
            return self.error(f"Failed to retrieve order status: {str(e)}")


# ── 4. Change Delivery Date ──────────────────────────────────────────────────

class ChangeDeliveryDatePG(BaseTool):
    def __init__(self, session_factory):
        self._session_factory = session_factory

    @property
    def name(self) -> str:
        return "change_delivery_date"

    @property
    def description(self) -> str:
        return (
            "Use this to change WHEN an order is delivered — the delivery DATE only. "
            "DO NOT use this for address changes — use change_delivery_address instead. "
            "Use when the customer asks to change, reschedule, or delay their delivery date. "
            "If the customer hasn't specified which order, call get_order_history first. "
            "Never call this tool with a guessed or invented date."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "Customer's email address"
                },
                "order_id": {
                    "type": "string",
                    "description": "The order ID confirmed by the customer"
                },
                "requested_date": {
                    "type": "string",
                    "description": "Requested new delivery date in YYYY-MM-DD format"
                }
            },
            "required": ["email", "order_id", "requested_date"]
        }

    async def execute(self, **kwargs: Any) -> dict:
        email          = kwargs.get("email", "").strip().lower()
        order_id       = kwargs.get("order_id", "").strip()
        requested_date = kwargs.get("requested_date", "").strip()
        session_id     = kwargs.get("session_id", "")

        if not email:
            return self.error("email is required.")
        if not order_id:
            return self.error("order_id is required.")
        if not requested_date:
            return self.error("requested_date is required.")

        try:
            from datetime import date as date_type, timedelta
            req_dt = date_type.fromisoformat(requested_date)
        except ValueError:
            return self.error(
                f"Invalid date format '{requested_date}'. Please use YYYY-MM-DD."
            )

        if req_dt <= datetime.utcnow().date():
            return self.error("Requested date must be in the future.")

        try:
            async with self._session_factory() as session:

                user_result = await session.execute(
                    text("SELECT id FROM users WHERE LOWER(email) = :email"),
                    {"email": email}
                )
                user = user_result.mappings().first()
                if not user:
                    return self.error(f"No account found for email: {email}")

                user_id = user["id"]

                customer_result = await session.execute(
                    text("SELECT customer_id FROM customers WHERE LOWER(email) = :email"),
                    {"email": email}
                )
                customer = customer_result.mappings().first()
                if not customer:
                    return self.error("No order history found for this account.")

                order_result = await session.execute(
                    text("""
                        SELECT order_id, order_status, order_estimated_delivery_date,
                               order_purchase_timestamp
                        FROM orders
                        WHERE order_id    = :order_id
                          AND customer_id = :customer_id
                    """),
                    {"order_id": order_id, "customer_id": customer["customer_id"]}
                )
                order = order_result.mappings().first()
                if not order:
                    return self.error(f"No order found with ID {order_id}.")

                status = order["order_status"]
                if status in ("delivered", "cancelled", "shipped"):
                    return self.success({
                        "outcome": "rejected",
                        "reason": (
                            "Your order has already been shipped and the delivery date "
                            "cannot be changed at this stage."
                        ),
                        "email":    email,
                        "order_id": order_id,
                    })

                # Earliest allowed delivery = order_purchase_timestamp + 3 days
                purchase_date = order["order_purchase_timestamp"]
                if hasattr(purchase_date, "date"):
                    purchase_date = purchase_date.date()
                earliest_allowed = purchase_date + timedelta(days=3)

                if req_dt < earliest_allowed:
                    return self.success({
                        "outcome": "rejected",
                        "reason": (
                            f"The earliest possible delivery date for your order is "
                            f"{earliest_allowed.strftime('%d %B %Y')} "
                            f"(3 days after your order was placed). "
                            f"Please choose a date on or after this."
                        ),
                        "earliest_allowed_date": str(earliest_allowed),
                        "email":    email,
                        "order_id": order_id,
                    })

                existing_result = await session.execute(
                    text("""
                        SELECT id, requested_date
                        FROM pending_requests
                        WHERE order_id = :order_id
                          AND status   = 'pending'
                        LIMIT 1
                    """),
                    {"order_id": order_id}
                )
                existing = existing_result.mappings().first()
                if existing:
                    return self.success({
                        "outcome": "already_pending",
                        "reason":  (
                            "There is already a pending date change request for this order. "
                            "Our team is reviewing it and will confirm within 24 hours."
                        ),
                        "existing_requested_date": str(existing["requested_date"]),
                        "request_id":              existing["id"],
                        "email":                   email,
                        "order_id":                order_id,
                    })

                now = datetime.now(timezone.utc)
                request_id = str(uuid.uuid4())

                await session.execute(
                    text("""
                        INSERT INTO pending_requests
                            (id, type, status, order_id, user_id,
                             requested_date, "current_date", session_id, created_at)
                        VALUES
                            (:id, :type, :status, :order_id, :user_id,
                             :requested_date, :current_date, :session_id, :created_at)
                    """),
                    {
                        "id":             request_id,
                        "type":           "date_change",
                        "status":         "pending",
                        "order_id":       order_id,
                        "user_id":        user_id,
                        "requested_date": req_dt,
                        "current_date":   order["order_estimated_delivery_date"],
                        "session_id":     session_id,
                        "created_at":     now,
                    }
                )
                await session.commit()

                try:
                    from backend.api.websocket import ws_manager
                    await ws_manager.broadcast_to_admins({
                        "type":       "new_request",
                        "request_id": request_id,
                        "order_id":   order_id,
                    })
                except Exception as broadcast_err:
                    logger.warning(f"Admin broadcast failed: {broadcast_err}")

                return self.success({
                    "outcome":        "pending_approval",
                    "request_id":     request_id,
                    "message":        (
                        "Your request has been submitted for review. "
                        "Our team will confirm within 24 hours."
                    ),
                    "requested_date": requested_date,
                    "email":          email,
                    "order_id":       order_id,
                })

        except Exception as e:
            logger.exception(f"change_delivery_date failed for {order_id}")
            return self.error(f"Failed to process date change request: {str(e)}")

# ── 5. Change Delivery Address ───────────────────────────────────────────────

class ChangeDeliveryAddressPG(BaseTool):
    def __init__(self, session_factory):
        self._session_factory = session_factory

    @property
    def name(self) -> str:
        return "change_delivery_address"

    @property
    def description(self) -> str:
        return (
            "Use this to change WHERE an order is delivered — street address, city, state, pincode. "
            "DO NOT use this for date changes — use change_delivery_date for that. "
            "Call directly with the new address — do NOT call get_order_details first. "
            "Only possible while status is 'processing' — updates immediately. "
            "Shipped, delivered, and cancelled orders cannot be changed. "
            "Collect full_address, city, state, pincode from the customer before calling. "
            "Never guess or invent address fields."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "email":        {"type": "string", "description": "Customer email"},
                "order_id":     {"type": "string", "description": "Order ID confirmed by customer"},
                "full_address": {"type": "string", "description": "New street address"},
                "city":         {"type": "string", "description": "New city"},
                "state":        {"type": "string", "description": "New state"},
                "pincode":      {"type": "string", "description": "New zip/pincode"},
            },
            "required": ["email", "order_id", "full_address", "city", "state", "pincode"]
        }

    async def execute(self, **kwargs: Any) -> dict:
        email        = kwargs.get("email", "").strip().lower()
        order_id     = kwargs.get("order_id", "").strip()
        full_address = kwargs.get("full_address", "").strip()
        city         = kwargs.get("city", "").strip()
        state        = kwargs.get("state", "").strip()
        pincode      = kwargs.get("pincode", "").strip()

        if not email:
            return self.error("email is required.")
        if not order_id:
            return self.error("order_id is required.")
        if not full_address:
            return self.error("full_address is required.")
        if not city:
            return self.error("city is required.")
        if not state:
            return self.error("state is required.")
        if not re.match(r'^\d{6}$', pincode):
            return self.error(
                "Invalid pincode — must be exactly 6 digits. "
                "Please confirm the correct pincode with the customer."
            )

        formatted_address = f"{full_address}, {city}, {state} - {pincode}"

        try:
            async with self._session_factory() as session:

                user_result = await session.execute(
                    text("SELECT id FROM users WHERE LOWER(email) = :email"),
                    {"email": email}
                )
                user = user_result.mappings().first()
                if not user:
                    return self.error(f"No account found for email: {email}")

                customer_result = await session.execute(
                    text("SELECT customer_id FROM customers WHERE LOWER(email) = :email"),
                    {"email": email}
                )
                customer = customer_result.mappings().first()
                if not customer:
                    return self.error("No order history found for this account.")

                order_result = await session.execute(
                    text("""
                        SELECT
                            order_id,
                            order_status,
                            delivery_full_address,
                            delivery_city,
                            delivery_state,
                            delivery_pincode
                        FROM orders
                        WHERE order_id    = :order_id
                          AND customer_id = :customer_id
                    """),
                    {"order_id": order_id, "customer_id": customer["customer_id"]}
                )
                order = order_result.mappings().first()
                if not order:
                    return self.error(f"No order found with ID {order_id}.")

                status = order["order_status"].lower()
                if status in ("delivered", "cancelled", "shipped"):
                    reason_map = {
                        "shipped":   (
                            "Your order has already been shipped and the address cannot be changed. "
                            "If the package is returned to us, we will reship to your correct address at no charge."
                        ),
                        "delivered": "Your order has already been delivered — the address cannot be changed.",
                        "cancelled": "Your order has been cancelled — the address cannot be changed.",
                    }
                    return self.success({
                        "outcome":  "rejected",
                        "reason":   reason_map[status],
                        "email":    email,
                        "order_id": order_id,
                    })

                if status == "processing":
                    await session.execute(
                        text("""
                            UPDATE orders
                            SET delivery_full_address = :full_address,
                                delivery_city         = :city,
                                delivery_state        = :state,
                                delivery_pincode      = :pincode
                            WHERE order_id = :order_id
                        """),
                        {
                            "full_address": full_address,
                            "city":         city,
                            "state":        state,
                            "pincode":      pincode,
                            "order_id":     order_id,
                        }
                    )
                    await session.commit()

                    try:
                        from backend.api.websocket import ws_manager
                        await ws_manager.broadcast_to_admins({
                            "type":        "address_updated_directly",
                            "order_id":    order_id,
                            "new_address": formatted_address,
                        })
                    except Exception as broadcast_err:
                        logger.warning(f"Admin broadcast failed: {broadcast_err}")

                    return self.success({
                        "outcome":     "updated_directly",
                        "message":     (
                            f"Your delivery address has been updated to: "
                            f"{formatted_address}."
                        ),
                        "new_address": formatted_address,
                        "email":       email,
                        "order_id":    order_id,
                    })

                return self.error(
                    f"Address cannot be changed for an order with status '{status}'."
                )

        except Exception as e:
            logger.exception(f"change_delivery_address failed for {order_id}")
            return self.error(f"Failed to process address change: {str(e)}")


# ── 6. Get Payment Info ──────────────────────────────────────────────────────

class GetPaymentInfoPG(BaseTool):
    def __init__(self, session_factory):
        self._session_factory = session_factory

    @property
    def name(self) -> str:
        return "get_payment_info"

    @property
    def description(self) -> str:
        return (
            "Retrieve full payment details for an order. "
            "Returns payment method(s), total paid, instalment breakdown, "
            "and each payment transaction line. "
            "If no order_id is given, automatically uses the customer's most recent order — "
            "this is the DEFAULT behaviour when a customer asks about 'my payment', "
            "'how did I pay', 'payment method', 'how much did I pay', or 'my receipt'. "
            "Only ask the customer to specify an order if they have already seen a list "
            "from get_order_history and explicitly want a different order's payment info."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "Customer's email address."
                },
                "order_id": {
                    "type": "string",
                    "description": (
                        "Specific order ID to look up. "
                        "OMIT this field to automatically use the customer's latest order."
                    )
                }
            },
            "required": ["email"]
        }

    async def execute(self, **kwargs: Any) -> dict:
        email    = kwargs.get("email", "").strip().lower()
        order_id = kwargs.get("order_id", "").strip() or None

        if not email:
            return self.error("email is required.")

        try:
            async with self._session_factory() as session:

                user_row = await session.execute(
                    text("SELECT id FROM users WHERE LOWER(email) = :email"),
                    {"email": email}
                )
                if not user_row.mappings().first():
                    return self.error(f"No account found for email: {email}")

                cust_row = await session.execute(
                    text("SELECT customer_id FROM customers WHERE LOWER(email) = :email"),
                    {"email": email}
                )
                customer = cust_row.mappings().first()
                if not customer:
                    return self.error("No order history found for this account.")

                customer_id = customer["customer_id"]

                is_latest = False
                if not order_id:
                    latest_row = await session.execute(
                        text("""
                            SELECT order_id
                            FROM   orders
                            WHERE  customer_id = :customer_id
                            ORDER  BY order_purchase_timestamp DESC
                            LIMIT  1
                        """),
                        {"customer_id": customer_id}
                    )
                    latest = latest_row.mappings().first()
                    if not latest:
                        return self.error("No orders found for this account.")
                    order_id  = latest["order_id"]
                    is_latest = True

                order_row = await session.execute(
                    text("""
                        SELECT
                            order_id,
                            order_status,
                            order_purchase_timestamp,
                            order_estimated_delivery_date
                        FROM  orders
                        WHERE order_id    = :order_id
                          AND customer_id = :customer_id
                        LIMIT 1
                    """),
                    {"order_id": order_id, "customer_id": customer_id}
                )
                order = order_row.mappings().first()
                if not order:
                    return self.error(f"No order found with ID '{order_id}' for this account.")

                pay_rows = await session.execute(
                    text("""
                        SELECT payment_type, payment_value
                        FROM   order_payments
                        WHERE  order_id = :order_id
                    """),
                    {"order_id": order_id}
                )
                payments = pay_rows.mappings().all()

                if not payments:
                    return self.error(f"No payment records found for order '{order_id}'.")

                total_paid    = sum(float(p["payment_value"]) for p in payments)
                payment_types = list({p["payment_type"] for p in payments})

                method_totals: dict[str, float] = {}
                for p in payments:
                    ptype = p["payment_type"]
                    method_totals[ptype] = round(
                        method_totals.get(ptype, 0.0) + float(p["payment_value"]), 2
                    )

                breakdown = [
                    {
                        "method": p["payment_type"],
                        "amount": round(float(p["payment_value"]), 2),
                    }
                    for p in payments
                ]

                return self.success({
                    "email":              email,
                    "order_id":           order_id,
                    "is_latest_order":    is_latest,
                    "order_status":       order["order_status"],
                    "ordered_at":         str(order["order_purchase_timestamp"]) if order["order_purchase_timestamp"] else None,
                    "estimated_delivery": str(order["order_estimated_delivery_date"]) if order["order_estimated_delivery_date"] else None,
                    "total_paid":         round(total_paid, 2),
                    "payment_methods":    payment_types,
                    "method_totals":      method_totals,
                    "transactions":       breakdown,
                    "message": (
                        "Here are the payment details for your "
                        + ("most recent order." if is_latest else f"order {order_id}.")
                    ),
                })

        except Exception as e:
            logger.exception(f"get_payment_info failed for email={email}, order_id={order_id}")
            return self.error(f"Failed to retrieve payment info: {str(e)}")


# ── 7. Get Seller Info ───────────────────────────────────────────────────────

class GetSellerInfoPG(BaseTool):
    def __init__(self, session_factory):
        self._session_factory = session_factory

    @property
    def name(self) -> str:
        return "get_seller_info"

    @property
    def description(self) -> str:
        return (
            "Retrieve seller information for every product in an order. "
            "For orders with multiple products, returns one seller entry per item "
            "including shop name, contact details, city, state, address, and the product they fulfilled. "
            "Use when the customer asks 'who is the seller', 'seller contact', 'seller details', "
            "'who sold me this', 'seller phone or email'. "
            "Defaults to the customer's most recent order if no order_id is given."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "email":    {"type": "string", "description": "Customer's email address."},
                "order_id": {"type": "string", "description": "Specific order ID. OMIT to use latest order."}
            },
            "required": ["email"]
        }

    async def execute(self, **kwargs) -> dict:
        email    = kwargs.get("email", "").strip().lower()
        order_id = kwargs.get("order_id", "").strip() or None

        if not email:
            return self.error("email is required.")

        try:
            async with self._session_factory() as session:

                user_row = await session.execute(
                    text("SELECT id FROM users WHERE LOWER(email) = :email"), {"email": email}
                )
                if not user_row.mappings().first():
                    return self.error(f"No account found for email: {email}")

                cust_row = await session.execute(
                    text("SELECT customer_id FROM customers WHERE LOWER(email) = :email"), {"email": email}
                )
                customer = cust_row.mappings().first()
                if not customer:
                    return self.error("No order history found for this account.")

                customer_id = customer["customer_id"]
                is_latest   = False

                if not order_id:
                    latest_row = await session.execute(
                        text("""
                            SELECT order_id FROM orders
                            WHERE customer_id = :customer_id
                            ORDER BY order_purchase_timestamp DESC LIMIT 1
                        """),
                        {"customer_id": customer_id}
                    )
                    latest = latest_row.mappings().first()
                    if not latest:
                        return self.error("No orders found for this account.")
                    order_id  = latest["order_id"]
                    is_latest = True

                order_check = await session.execute(
                    text("""
                        SELECT order_id FROM orders
                        WHERE order_id = :order_id AND customer_id = :customer_id LIMIT 1
                    """),
                    {"order_id": order_id, "customer_id": customer_id}
                )
                if not order_check.mappings().first():
                    return self.error(f"No order found with ID '{order_id}' for this account.")

                rows = await session.execute(
                    text("""
                        SELECT
                            oi.order_item_id,
                            oi.price,
                            oi.freight_value,
                            p.product_id,
                            p.product_name,
                            p.product_category_name,
                            s.seller_id,
                            s.shop_name,
                            s.seller_city,
                            s.seller_state,
                            s.phone,
                            s.email        AS seller_email,
                            s.full_address,
                            s.pincode
                        FROM   order_items oi
                        JOIN   products p ON p.product_id = oi.product_id
                        JOIN   sellers  s ON s.seller_id  = oi.seller_id
                        WHERE  oi.order_id = :order_id
                        ORDER  BY oi.order_item_id ASC
                    """),
                    {"order_id": order_id}
                )
                rows = rows.mappings().all()

                if not rows:
                    return self.error(f"No items or seller data found for order '{order_id}'.")

                return self.success({
                    "email":           email,
                    "order_id":        order_id,
                    "is_latest_order": is_latest,
                    "total_items":     len(rows),
                    "items": [
                        {
                            "order_item_id":    row["order_item_id"],
                            "product_name":     row["product_name"],
                            "product_category": row["product_category_name"],
                            "item_price":       float(row["price"]),
                            "freight_value":    float(row["freight_value"]),
                            "seller": {
                                "seller_id": row["seller_id"],
                                "shop_name": row["shop_name"],
                                "phone":     row["phone"],
                                "email":     row["seller_email"],
                                "city":      row["seller_city"],
                                "state":     row["seller_state"],
                                "address":   row["full_address"],
                                "pincode":   row["pincode"],
                            }
                        }
                        for row in rows
                    ],
                    "message": (
                        "Here are the seller details for your "
                        + ("most recent order." if is_latest else f"order {order_id}.")
                    ),
                })

        except Exception as e:
            logger.exception(f"get_seller_info failed for email={email}, order_id={order_id}")
            return self.error(f"Failed to retrieve seller info: {str(e)}")
        


# ── 8. Get User Profile ──────────────────────────────────────────────────────

class GetUserProfilePG(BaseTool):
    def __init__(self, session_factory):
        self._session_factory = session_factory

    @property
    def name(self) -> str:
        return "get_user_profile"

    @property
    def description(self) -> str:
        return (
            "Retrieve a customer's profile: personal info, account status, "
            "loyalty tier and points, and member since date. "
            "Use when the customer asks 'my profile', 'my account', 'my details', "
            "'what tier am I', 'my loyalty points', 'when did I join'."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Customer's email address."}
            },
            "required": ["email"]
        }

    async def execute(self, **kwargs) -> dict:
        email = kwargs.get("email", "").strip().lower()
        if not email:
            return self.error("email is required.")

        try:
            async with self._session_factory() as session:

                row = await session.execute(
                    text("""
                        SELECT
                            u.name,
                            u.surname,
                            u.email,
                            u.role,
                            u.account_status,
                            u.is_active,
                            u.loyalty_tier,
                            u.loyalty_points,
                            u.created_at,
                            c.phone,
                            c.customer_city,
                            c.customer_state,
                            c.full_address,
                            c.pincode
                        FROM  users u
                        JOIN  customers c ON LOWER(c.email) = LOWER(u.email)
                        WHERE LOWER(u.email) = :email
                        LIMIT 1
                    """),
                    {"email": email}
                )
                profile = row.mappings().first()

                if not profile:
                    return self.error(f"No account found for email: {email}")

                return self.success({
                    "name":           f"{profile['name']} {profile['surname']}",
                    "email":          profile["email"],
                    "phone":          profile["phone"],
                    "city":           profile["customer_city"],
                    "state":          profile["customer_state"],
                    "address":        profile["full_address"],
                    "pincode":        profile["pincode"],
                    "role":           profile["role"],
                    "account_status": profile["account_status"],
                    "is_active":      profile["is_active"],
                    "loyalty_tier":   profile["loyalty_tier"],
                    "loyalty_points": profile["loyalty_points"],
                    "member_since":   str(profile["created_at"]) if profile["created_at"] else None,
                })

        except Exception as e:
            logger.exception(f"get_user_profile failed for email={email}")
            return self.error(f"Failed to retrieve profile: {str(e)}")
        
# ── Update User Profile ──────────────────────────────────────────────────────

class UpdateUserProfilePG(BaseTool):
    def __init__(self, session_factory):
        self._session_factory = session_factory

    @property
    def name(self) -> str:
        return "update_user_profile"

    @property
    def description(self) -> str:
        return (
            "Update a customer's profile details — name, phone, or address. "
            "Only update fields the customer explicitly asks to change. "
            "Never update email, password, loyalty tier, or account status. "
            "Call get_user_profile first if you need to show current values."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "email":    {"type": "string", "description": "Customer email (to identify account)"},
                "name":     {"type": "string", "description": "New first name"},
                "surname":  {"type": "string", "description": "New last name"},
                "phone":    {"type": "string", "description": "New phone number"},
                "address":  {"type": "string", "description": "New full street address"},
                "city":     {"type": "string", "description": "New city"},
                "state":    {"type": "string", "description": "New state"},
                "pincode":  {"type": "string", "description": "New pincode"},
            },
            "required": ["email"]
        }

    async def execute(self, **kwargs: Any) -> dict:
        email   = kwargs.get("email", "").strip().lower()
        name    = kwargs.get("name",    "").strip()
        surname = kwargs.get("surname", "").strip()
        phone   = kwargs.get("phone",   "").strip()
        address = kwargs.get("address", "").strip()
        city    = kwargs.get("city",    "").strip()
        state   = kwargs.get("state",   "").strip()
        pincode = kwargs.get("pincode", "").strip()

        if not email:
            return self.error("email is required.")

        # Must have at least one field to update
        user_fields     = {k: v for k, v in {"name": name, "surname": surname}.items() if v}
        customer_fields = {k: v for k, v in {
            "phone":           phone,
            "full_address":    address,
            "customer_city":   city,
            "customer_state":  state,
            "pincode":         pincode,
        }.items() if v}

        if not user_fields and not customer_fields:
            return self.error(
                "No fields to update — please specify at least one field to change."
            )

        try:
            async with self._session_factory() as session:

                # ── 1. Verify user exists ────────────────────────────────────
                user_result = await session.execute(
                    text("SELECT id FROM users WHERE LOWER(email) = :email"),
                    {"email": email}
                )
                user = user_result.mappings().first()
                if not user:
                    return self.error(f"No account found for email: {email}")
                active_orders = []
                # ── 2. Update users table if needed ──────────────────────────
                if user_fields:
                    set_clause = ", ".join(f"{k} = :{k}" for k in user_fields)
                    await session.execute(
                        text(f"UPDATE users SET {set_clause} WHERE LOWER(email) = :email"),
                        {**user_fields, "email": email}
                    )

                # ── 3. Update customers table if needed ──────────────────────
                if customer_fields and any(k in customer_fields for k in 
                    ("full_address", "customer_city", "customer_state", "pincode")):

                    # Find active orders that haven't had delivery address set yet
                    active_orders_result = await session.execute(
                        text("""
                            SELECT o.order_id
                            FROM orders o
                            JOIN customers c ON c.customer_id = o.customer_id
                            WHERE LOWER(c.email) = :email
                            AND o.order_status NOT IN ('delivered', 'cancelled')
                            AND o.delivery_full_address IS NULL
                        """),
                        {"email": email}
                    )
                    active_orders = active_orders_result.mappings().all()

                    if active_orders:
                        # Fetch current address from customers before overwriting
                        current_result = await session.execute(
                            text("""
                                SELECT full_address, customer_city, customer_state, pincode
                                FROM customers
                                WHERE LOWER(email) = :email
                            """),
                            {"email": email}
                        )
                        current = current_result.mappings().first()

                        if current:
                            order_ids = [o["order_id"] for o in active_orders]
                            for order_id in order_ids:
                                await session.execute(
                                    text("""
                                        UPDATE orders
                                        SET delivery_full_address = :full_address,
                                            delivery_city         = :city,
                                            delivery_state        = :state,
                                            delivery_pincode      = :pincode
                                        WHERE order_id = :order_id
                                    """),
                                    {
                                        "full_address": current["full_address"],
                                        "city":         current["customer_city"],
                                        "state":        current["customer_state"],
                                        "pincode":      current["pincode"],
                                        "order_id":     order_id,
                                    }
                                )

                # ── 4. Now safe to update customers table ────────────────────────────
                if customer_fields:
                    set_clause = ", ".join(f"{k} = :{k}" for k in customer_fields)
                    await session.execute(
                        text(f"UPDATE customers SET {set_clause} WHERE LOWER(email) = :email"),
                        {**customer_fields, "email": email}
                    )

                # ── 5. Update users table if needed ──────────────────────────
                if user_fields:
                    set_clause = ", ".join(f"{k} = :{k}" for k in user_fields)
                    await session.execute(
                        text(f"UPDATE users SET {set_clause} WHERE LOWER(email) = :email"),
                        {**user_fields, "email": email}
                    )

                await session.commit()

                # ── 6. Build summary ──────────────────────────────────────────
                changed = []
                if name:    changed.append(f"first name to '{name}'")
                if surname: changed.append(f"last name to '{surname}'")
                if phone:   changed.append(f"phone to '{phone}'")
                if address: changed.append(f"address to '{address}'")
                if city:    changed.append(f"city to '{city}'")
                if state:   changed.append(f"state to '{state}'")
                if pincode: changed.append(f"pincode to '{pincode}'")

                snapshot_note = ""
                if active_orders and any(k in customer_fields for k in
                    ("full_address", "customer_city", "customer_state", "pincode")):
                    snapshot_note = (
                        f" Your {len(active_orders)} active order(s) will continue "
                        f"to ship to your previous address. Use 'change delivery address' "
                        f"per order if you want to redirect them."
                    )

                return self.success({
                    "outcome":        "updated",
                    "message":        f"Profile updated successfully: {', '.join(changed)}.{snapshot_note}",
                    "updated_fields": list(user_fields.keys()) + list(customer_fields.keys()),
                    "active_orders_snapshotted": len(active_orders) if active_orders else 0,
                })

        except Exception as e:
            logger.exception(f"update_user_profile failed for {email}")
            return self.error(f"Failed to update profile: {str(e)}")
        
# ── 6. Initiate Return ───────────────────────────────────────────────────────

class InitiateReturnPG(BaseTool):
    def __init__(self, session_factory):
        self._session_factory = session_factory

    @property
    def name(self) -> str:
        return "initiate_return"

    @property
    def description(self) -> str:
        return (
            "Initiate a return for a delivered order. "
            "DO NOT use for cancellations — use cancel_order for that. "
            "Checks 30-day return window (45 days for Platinum members). "
            "Before calling, confirm: return reason, refund method, and which items. "
            "If customer hasn't specified order, call get_order_history first."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "email":    {"type": "string", "description": "Customer email"},
                "order_id": {"type": "string", "description": "Order ID confirmed by customer"},
                "reason": {
                    "type": "string",
                    "enum": [
                        "defective_damaged",
                        "wrong_item_received",
                        "not_as_described",
                        "changed_mind",
                        "size_fit_issue"
                    ],
                    "description": "Return reason"
                },
                "refund_method": {
                    "type": "string",
                    "enum": ["original_payment", "store_credit", "bank_transfer"],
                    "description": "Preferred refund method"
                },
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Product names to return"
                },
            },
            "required": ["email", "order_id", "reason", "refund_method", "items"]
        }

    async def execute(self, **kwargs: Any) -> dict:
        email         = kwargs.get("email", "").strip().lower()
        order_id      = kwargs.get("order_id", "").strip()
        reason        = kwargs.get("reason", "").strip()
        refund_method = kwargs.get("refund_method", "").strip()
        items         = kwargs.get("items", [])
        session_id     = kwargs.get("session_id")
        if not email:
            return self.error("email is required.")
        if not order_id:
            return self.error("order_id is required.")
        if not reason:
            return self.error("reason is required.")
        if not refund_method:
            return self.error("refund_method is required.")
        if not items:
            return self.error("At least one item must be specified.")

        try:
            async with self._session_factory() as session:

                user_result = await session.execute(
                    text("SELECT id, loyalty_tier FROM users WHERE LOWER(email) = :email"),
                    {"email": email}
                )
                user = user_result.mappings().first()
                if not user:
                    return self.error(f"No account found for email: {email}")

                customer_result = await session.execute(
                    text("SELECT customer_id FROM customers WHERE LOWER(email) = :email"),
                    {"email": email}
                )
                customer = customer_result.mappings().first()
                if not customer:
                    return self.error("No order history found for this account.")

                order_result = await session.execute(
                    text("""
                        SELECT
                            order_id,
                            order_status,
                            order_delivered_customer_date,
                            order_estimated_delivery_date
                        FROM orders
                        WHERE order_id    = :order_id
                          AND customer_id = :customer_id
                    """),
                    {"order_id": order_id, "customer_id": customer["customer_id"]}
                )
                order = order_result.mappings().first()
                if not order:
                    return self.error(f"No order found with ID {order_id}.")

                status = order["order_status"]
                if status != "delivered":
                    return self.success({
                        "outcome": "rejected",
                        "reason": (
                            f"Your order is currently '{status}'. "
                            "Returns can only be initiated after the order has been delivered."
                        ),
                        "current_status": status,
                    })

                delivery_date = (
                    order["order_delivered_customer_date"]
                    or order["order_estimated_delivery_date"]
                )
                if not delivery_date:
                    return self.error(
                        "Order is missing delivery date — cannot evaluate return window."
                    )

                if isinstance(delivery_date, datetime):
                    if delivery_date.tzinfo is None:
                        delivery_date = delivery_date.replace(tzinfo=timezone.utc)
                elif isinstance(delivery_date, date):
                    delivery_date = datetime(
                        delivery_date.year,
                        delivery_date.month,
                        delivery_date.day,
                        tzinfo=timezone.utc
                    )

                loyalty_tier  = user["loyalty_tier"] or "Bronze"
                return_window = 45 if loyalty_tier == "Platinum" else 30
                now           = datetime.now(timezone.utc)
                days_elapsed  = (now - delivery_date).days

                if days_elapsed > return_window:
                    return self.success({
                        "outcome": "rejected",
                        "reason": (
                            f"Your return window has expired. "
                            f"The order was delivered on "
                            f"{delivery_date.strftime('%B %d, %Y')} "
                            f"({days_elapsed} days ago). "
                            f"Return window is {return_window} days"
                            f"{' for Platinum members' if loyalty_tier == 'Platinum' else ''}."
                        ),
                        "delivered_on":  delivery_date.date().isoformat(),
                        "return_window": return_window,
                        "days_elapsed":  days_elapsed,
                    })

                existing_result = await session.execute(
                    text("""
                        SELECT id FROM pending_requests
                        WHERE order_id = :order_id
                          AND type     = 'return_request'
                          AND status   = 'pending'
                        LIMIT 1
                    """),
                    {"order_id": order_id}
                )
                existing = existing_result.mappings().first()
                if existing:
                    return self.success({
                        "outcome":    "already_pending",
                        "reason":     (
                            "There is already a pending return request for this order. "
                            "Our team will confirm within 24 hours."
                        ),
                        "request_id": existing["id"],
                    })

                leafy_covers = {
                    "defective_damaged",
                    "wrong_item_received",
                    "not_as_described"
                }
                shipping_covered_by = (
                    "leafy" if reason in leafy_covers else "customer"
                )

                request_id = str(uuid.uuid4())
                now        = datetime.now(timezone.utc)

                await session.execute(
                    text("""
                        INSERT INTO pending_requests (
                            id, type, status, order_id, user_id,
                            reason, items, refund_method,
                            return_shipping_covered_by,
                            session_id, created_at
                        ) VALUES (
                            :id, :type, :status, :order_id, :user_id,
                            :reason, :items, :refund_method,
                            :shipping_covered_by,
                            :session_id, :created_at
                        )
                    """),
                    {
                        "id":                  request_id,
                        "type":                "return_request",
                        "status":              "pending",
                        "order_id":            order_id,
                        "user_id":             user["id"],
                        "reason":              reason,
                        "items":               json.dumps(items),
                        "refund_method":       refund_method,
                        "shipping_covered_by": shipping_covered_by,
                        "created_at":          now,
                        "session_id":          session_id
                    }
                )
                await session.commit()

                try:
                    from backend.api.websocket import ws_manager
                    await ws_manager.broadcast_to_admins({
                        "type":         "new_request",
                        "request_id":   request_id,
                        "order_id":     order_id,
                        "request_type": "return_request",
                    })
                except Exception as broadcast_err:
                    logger.warning(f"Admin broadcast failed: {broadcast_err}")

                return self.success({
                    "email":      email,
                    "outcome":    "pending_approval",
                    "request_id": request_id,
                    "message": (
                        "Your return request has been submitted and is pending approval. "
                        "Our team will review it within 24 hours and send you an RMA number. "
                        f"Return shipping will be covered by "
                        f"{'Leafy' if shipping_covered_by == 'leafy' else 'you (the customer)'}."
                    ),
                    "items":                      items,
                    "reason":                     reason,
                    "refund_method":              refund_method,
                    "return_shipping_covered_by": shipping_covered_by,
                })

        except Exception as e:
            logger.exception(f"initiate_return failed for {order_id}")
            return self.error(f"Failed to process return request: {str(e)}")

class ReportMissingItemPG(BaseTool):
    def __init__(self, session_factory):
        self._session_factory = session_factory

    @property
    def name(self) -> str:
        return "report_missing_item"

    @property
    def description(self) -> str:
        return (
            "Report one or more missing items from a delivered order. "
            "DO NOT use for wrong items received — use report_wrong_item for that. "
            "Order must be 'delivered' to use this tool. "
            "Before calling, confirm: which items are missing and package condition. "
            "If customer hasn't specified order, call get_order_history first. "
            "Always call get_order_details first to confirm which items exist in the order."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "email":             {"type": "string",  "description": "Customer email"},
                "order_id":          {"type": "string",  "description": "Order ID confirmed by customer"},
                "missing_items":     {
                    "type":  "array",
                    "items": {"type": "string"},
                    "description": "Names of missing items exactly as they appear in the order"
                },
                "package_condition": {
                    "type": "string",
                    "enum": ["intact", "damaged", "tampered"],
                    "description": "Condition of the package when it arrived"
                },
            },
            "required": ["email", "order_id", "missing_items", "package_condition"]
        }

    async def execute(self, **kwargs: Any) -> dict:
        email             = kwargs.get("email", "").strip().lower()
        order_id          = kwargs.get("order_id", "").strip()
        missing_items     = kwargs.get("missing_items", [])
        package_condition = kwargs.get("package_condition", "").strip()
        session_id           = kwargs.get("session_id")

        # ── Input validation ─────────────────────────────────────────────────
        if not email:
            return self.error("email is required.")
        if not order_id:
            return self.error("order_id is required.")
        if not missing_items:
            return self.error("At least one missing item must be specified.")
        if not package_condition:
            return self.error("package_condition is required.")

        try:
            async with self._session_factory() as session:

                # ── 1. Verify user ───────────────────────────────────────────
                user_result = await session.execute(
                    text("SELECT id FROM users WHERE LOWER(email) = :email"),
                    {"email": email}
                )
                user = user_result.mappings().first()
                if not user:
                    return self.error(f"No account found for email: {email}")

                # ── 2. Verify order ownership ────────────────────────────────
                customer_result = await session.execute(
                    text("SELECT customer_id FROM customers WHERE LOWER(email) = :email"),
                    {"email": email}
                )
                customer = customer_result.mappings().first()
                if not customer:
                    return self.error("No order history found for this account.")

                order_result = await session.execute(
                    text("""
                        SELECT
                            o.order_id,
                            o.order_status,
                            STRING_AGG(p.product_name, ', ') AS products
                        FROM orders o
                        LEFT JOIN order_items oi ON oi.order_id   = o.order_id
                        LEFT JOIN products    p  ON p.product_id  = oi.product_id
                        WHERE o.order_id    = :order_id
                          AND o.customer_id = :customer_id
                        GROUP BY o.order_id, o.order_status
                    """),
                    {"order_id": order_id, "customer_id": customer["customer_id"]}
                )
                order = order_result.mappings().first()
                if not order:
                    return self.error(f"No order found with ID {order_id}.")

                # ── 3. Must be delivered ─────────────────────────────────────
                status = order["order_status"]
                if status != "delivered":
                    return self.success({
                        "outcome": "rejected",
                        "reason": (
                            f"Your order is currently '{status}'. "
                            "Missing item reports can only be made after "
                            "the order has been delivered."
                        ),
                        "current_status": status,
                    })

                # ── 4. Validate missing items exist in the order ─────────────
                order_products = [
                    p.strip().lower()
                    for p in (order["products"] or "").split(",")
                ]
                invalid_items = [
                    item for item in missing_items
                    if item.strip().lower() not in order_products
                ]
                if invalid_items:
                    return self.success({
                        "outcome": "invalid_items",
                        "reason": (
                            f"The following items were not found in this order: "
                            f"{', '.join(invalid_items)}. "
                            f"Order contains: {order['products']}."
                        ),
                        "invalid_items":  invalid_items,
                        "order_products": order["products"],
                    })

                # ── 5. Check for existing pending report ─────────────────────
                existing_result = await session.execute(
                    text("""
                        SELECT id FROM pending_requests
                        WHERE order_id = :order_id
                          AND type     = 'missing_item'
                          AND status   = 'pending'
                        LIMIT 1
                    """),
                    {"order_id": order_id}
                )
                existing = existing_result.mappings().first()
                if existing:
                    return self.success({
                        "outcome":    "already_pending",
                        "reason":     (
                            "There is already a pending missing item report for this order. "
                            "Our team is investigating and will respond within 24 hours."
                        ),
                        "request_id": existing["id"],
                    })

                # ── 6. Determine resolution type hint for admin ──────────────
                # Give admin a starting point based on package condition
                resolution_hint = {
                    "intact":   "investigate",  # warehouse error likely
                    "damaged":  "investigate",  # carrier damage, file claim
                    "tampered": "investigate",  # theft likely, escalate
                }.get(package_condition, "investigate")

                # ── 7. Insert pending request ────────────────────────────────
                request_id = str(uuid.uuid4())
                now        = datetime.now(timezone.utc)

                await session.execute(
                    text("""
                        INSERT INTO pending_requests (
                            id, type, status, order_id, user_id,
                            reported_items, package_condition,
                            resolution_type, session_id, created_at
                        ) VALUES (
                            :id, :type, :status, :order_id, :user_id,
                            :reported_items, :package_condition,
                            :resolution_type, :session_id, :created_at
                        )
                    """),
                    {
                        "id":               request_id,
                        "type":             "missing_item",
                        "status":           "pending",
                        "order_id":         order_id,
                        "user_id":          user["id"],
                        "reported_items":   json.dumps(missing_items),
                        "package_condition": package_condition,
                        "resolution_type":  resolution_hint,
                        "session_id":      session_id,
                        "created_at":       now,
                    }
                )
                await session.commit()

                # ── 8. Broadcast to admin CRM ────────────────────────────────
                try:
                    from backend.api.websocket import ws_manager
                    await ws_manager.broadcast_to_admins({
                        "type":         "new_request",
                        "request_id":   request_id,
                        "order_id":     order_id,
                        "request_type": "missing_item",
                    })
                except Exception as broadcast_err:
                    logger.warning(f"Admin broadcast failed: {broadcast_err}")

                # ── 9. Build customer message based on package condition ──────
                condition_context = {
                    "intact":   (
                        "This appears to be a warehouse packing error. "
                        "Our team will investigate and reship the missing item(s) "
                        "or process a refund if unavailable."
                    ),
                    "damaged":  (
                        "Since your package arrived damaged, this may be a carrier issue. "
                        "Our team will file a claim and arrange a replacement or refund."
                    ),
                    "tampered": (
                        "Since your package arrived tampered, we are treating this as urgent. "
                        "Our team will escalate this immediately."
                    ),
                }.get(package_condition, "Our team will investigate.")

                return self.success({
                    "outcome":    "pending_approval",
                    "request_id": request_id,
                    "message": (
                        f"Your missing item report has been submitted. "
                        f"{condition_context} "
                        f"We will update you within 24 hours."
                    ),
                    "missing_items":     missing_items,
                    "package_condition": package_condition,
                    "order_id":          order_id,
                })

        except Exception as e:
            logger.exception(f"report_missing_item failed for {order_id}")
            return self.error(f"Failed to submit missing item report: {str(e)}")

class GetRequestStatusPG(BaseTool):
    def __init__(self, session_factory):
        self._session_factory = session_factory

    @property
    def name(self) -> str:
        return "get_request_status"

    @property
    def description(self) -> str:
        return (
            "Get the status of any pending or resolved requests for a customer. "
            "Use when customer asks about their return, cancellation, date change, "
            "address change, or missing item report status. "
            "Returns all requests — filter by type if customer specifies one."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Customer email"},
                "type":  {
                    "type": "string",
                    "enum": [
                        "return_request",
                        "cancel_request",
                        "date_change",
                        "address_change",
                        "missing_item",
                    ],
                    "description": "Optional — filter by request type if customer specifies"
                },
            },
            "required": ["email"]
        }

    async def execute(self, **kwargs: Any) -> dict:
        email        = kwargs.get("email", "").strip().lower()
        filter_type  = kwargs.get("type", "").strip()

        if not email:
            return self.error("email is required.")

        try:
            async with self._session_factory() as session:

                # ── 1. Verify user ───────────────────────────────────────────
                user_result = await session.execute(
                    text("SELECT id FROM users WHERE LOWER(email) = :email"),
                    {"email": email}
                )
                user = user_result.mappings().first()
                if not user:
                    return self.error(f"No account found for email: {email}")

                # ── 2. Build query ───────────────────────────────────────────
                query = """
                    SELECT
                        id,
                        type,
                        status,
                        order_id,
                        created_at,
                        resolved_at,
                        resolution_note,
                        requested_date,
                        requested_address,
                        requested_city,
                        requested_state,
                        requested_pincode,
                        reason,
                        items,
                        refund_method,
                        return_shipping_covered_by,
                        reported_items,
                        package_condition
                    FROM pending_requests
                    WHERE user_id = :user_id
                """
                params = {"user_id": user["id"]}

                if filter_type:
                    query += " AND type = :type"
                    params["type"] = filter_type

                query += " ORDER BY created_at DESC LIMIT 10"

                result = await session.execute(text(query), params)
                rows   = result.mappings().all()

                if not rows:
                    msg = (
                        f"No {filter_type.replace('_', ' ')} requests found."
                        if filter_type
                        else "No requests found for this account."
                    )
                    return self.success({
                        "outcome":  "no_requests",
                        "message":  msg,
                        "requests": [],
                    })

                # ── 3. Serialize ─────────────────────────────────────────────
                requests = []
                for row in rows:
                    entry = {
                        "request_id":   row["id"],
                        "type":         row["type"],
                        "status":       row["status"],
                        "order_id":     row["order_id"],
                        "created_at":   str(row["created_at"]) if row["created_at"] else None,
                        "resolved_at":  str(row["resolved_at"]) if row["resolved_at"] else None,
                        "resolution_note": row["resolution_note"],
                    }

                    # Type-specific fields
                    if row["type"] == "date_change":
                        entry["requested_date"] = str(row["requested_date"]) if row["requested_date"] else None

                    elif row["type"] == "address_change":
                        entry["requested_address"] = row["requested_address"]
                        entry["requested_city"]    = row["requested_city"]
                        entry["requested_state"]   = row["requested_state"]
                        entry["requested_pincode"] = row["requested_pincode"]

                    elif row["type"] == "return_request":
                        entry["reason"]                      = row["reason"]
                        entry["items"]                       = json.loads(row["items"]) if row["items"] else []
                        entry["refund_method"]               = row["refund_method"]
                        entry["return_shipping_covered_by"]  = row["return_shipping_covered_by"]

                    elif row["type"] == "missing_item":
                        entry["reported_items"]    = json.loads(row["reported_items"]) if row["reported_items"] else []
                        entry["package_condition"] = row["package_condition"]

                    requests.append(entry)

                return self.success({
                    "outcome":  "found",
                    "requests": requests,
                    "total":    len(requests),
                })

        except Exception as e:
            logger.exception(f"get_request_status failed for {email}")
            return self.error(f"Failed to fetch request status: {str(e)}")


# ── 10. Cancel Order ─────────────────────────────────────────────────────────
#
# Two paths based on order_status:
#   processing / Processing  →  cancel immediately, set order_status = 'Cancelled'
#   invoiced   / Invoiced    →  insert pending_request, admin must approve
#   anything else            →  return outcome='not_cancellable' with explanation

class CancelOrderPG(BaseTool):
    def __init__(self, session_factory):
        self._session_factory = session_factory

    @property
    def name(self) -> str:
        return "cancel_order"

    @property
    def description(self) -> str:
        return (
            "Cancel a customer's order. "
            "Processing orders are cancelled immediately — no admin needed. "
            "Invoiced orders require admin approval — collect a cancellation reason "
            "from the customer before calling this tool. "
            "Shipped, delivered, created, approved, and already-cancelled orders "
            "cannot be cancelled — advise shipped/delivered customers to return instead. "
            "DO NOT use for returns — use initiate_return for delivered orders. "
            "Always confirm the order_id with the customer before calling."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "Customer's email address."
                },
                "order_id": {
                    "type": "string",
                    "description": "The exact order ID the customer wants to cancel."
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "Cancellation reason collected from the customer. "
                        "Required for invoiced orders before calling this tool. "
                        "For processing orders use 'not_required'. "
                        "Examples: 'changed_mind', 'ordered_by_mistake', "
                        "'found_better_price', 'delivery_too_slow', 'other'."
                    ),
                },
            },
            "required": ["email", "order_id", "reason"],
        }

    async def execute(self, **kwargs: Any) -> dict:
        email    = kwargs.get("email", "").strip().lower()
        order_id = kwargs.get("order_id", "").strip()
        reason   = kwargs.get("reason", "other").strip()
        session_id  = kwargs.get("session_id")

        if not email:
            return self.error("email is required.")
        if not order_id:
            return self.error("order_id is required.")

        try:
            async with self._session_factory() as session:

                # ── 1. Verify user ───────────────────────────────────────────
                user_result = await session.execute(
                    text("SELECT id FROM users WHERE LOWER(email) = :email LIMIT 1"),
                    {"email": email}
                )
                user = user_result.mappings().first()
                if not user:
                    return self.error(f"No account found for email: {email}")

                user_id = user["id"]

                # ── 2. Verify order ownership ────────────────────────────────
                customer_result = await session.execute(
                    text("SELECT customer_id FROM customers WHERE LOWER(email) = :email"),
                    {"email": email}
                )
                customer = customer_result.mappings().first()
                if not customer:
                    return self.error("No order history found for this account.")

                order_result = await session.execute(
                    text("""
                        SELECT order_id, order_status
                        FROM   orders
                        WHERE  order_id    = :order_id
                          AND  customer_id = :customer_id
                        LIMIT  1
                    """),
                    {"order_id": order_id, "customer_id": customer["customer_id"]}
                )
                order = order_result.mappings().first()
                if not order:
                    return self.error(f"No order found with ID {order_id}.")

                raw_status   = order["order_status"] or ""
                status_lower = raw_status.lower()

                # ── 3. Already cancelled ─────────────────────────────────────
                if status_lower == "cancelled":
                    return self.success({
                        "outcome":      "already_cancelled",
                        "order_id":     order_id,
                        "order_status": raw_status,
                        "message": (
                            "This order has already been cancelled. "
                            "Your refund should arrive within 3–5 business days "
                            "to your original payment method."
                        ),
                    })

                # ── 4. Processing → auto-cancel immediately ───────────────────
                if status_lower == "processing":
                    await session.execute(
                        text("""
                            UPDATE orders
                            SET    order_status = 'Cancelled'
                            WHERE  order_id = :order_id
                        """),
                        {"order_id": order_id}
                    )
                    await session.commit()

                    logger.info(
                        f"CancelOrderPG: order {order_id} cancelled immediately "
                        f"(user_id={user_id}, reason={reason})"
                    )

                    return self.success({
                        "outcome":         "cancelled",
                        "order_id":        order_id,
                        "refund_method":   "original_payment",
                        "refund_timeline": "3–5 business days",
                        "message": (
                            f"Your order #{order_id[-8:].upper()} has been cancelled. "
                            "Your refund will be returned to your original payment method "
                            "within 3–5 business days."
                        ),
                    })

                # ── 5. Invoiced → create pending_request for admin approval ───
                if status_lower == "invoiced":
                    # Guard against duplicate pending cancellation
                    existing_result = await session.execute(
                        text("""
                            SELECT id FROM pending_requests
                            WHERE  order_id = :order_id
                              AND  type     = 'cancellation_request'
                              AND  status   = 'pending'
                            LIMIT  1
                        """),
                        {"order_id": order_id}
                    )
                    if existing_result.mappings().first():
                        return self.success({
                            "outcome":  "already_pending",
                            "order_id": order_id,
                            "message": (
                                "A cancellation request for this order is already awaiting "
                                "admin review. We'll notify you as soon as a decision is made."
                            ),
                        })

                    request_id = str(uuid.uuid4())
                    now_dt     = datetime.now(timezone.utc)
                    now_date   = now_dt.date()

                    await session.execute(
                        text("""
                    INSERT INTO pending_requests (
                        id,
                        type,
                        status,
                        order_id,
                        user_id,
                        requested_date,
                        "current_date",
                        session_id,
                        reason,
                        refund_method,
                        created_at
                    ) VALUES (
                        :id,
                        'cancellation_request',
                        'pending',
                        :order_id,
                        :user_id,
                        :now_date,
                        :now_date,
                        :session_id,
                        :reason,
                        'original_payment',
                        :now_dt
                    )
                """),
                {
                    "id":         request_id,
                    "order_id":   order_id,
                    "user_id":    user_id,
                    "now_dt":     now_dt,
                    "now_date":   now_date,
                    "reason":     reason,
                    "session_id": session_id,
                }
            )
                    await session.commit()

                    logger.info(
                        f"CancelOrderPG: pending_request={request_id} created for "
                        f"order {order_id} (user_id={user_id}, reason={reason})"
                    )

                    # Broadcast to admin CRM so the queue updates immediately
                    try:
                        from backend.api.websocket import ws_manager
                        await ws_manager.broadcast_to_admins({
                            "type":         "new_request",
                            "request_id":   request_id,
                            "order_id":     order_id,
                            "request_type": "cancellation_request",
                        })
                    except Exception as broadcast_err:
                        logger.warning(f"Admin broadcast failed: {broadcast_err}")

                    return self.success({
                        "email":      email,
                        "outcome":    "request_submitted",
                        "order_id":   order_id,
                        "request_id": request_id,
                        "message": (
                            f"Your cancellation request for order "
                            f"#{order_id[-8:].upper()} has been submitted "
                            "and is pending admin review. "
                            "We'll notify you once a decision has been made. "
                            "If approved, your refund will be returned to your "
                            "original payment method within 3–5 business days."
                        ),
                    })

                # ── 6. Not cancellable ────────────────────────────────────────
                if status_lower in ("shipped", "delivered"):
                    tip = (
                        "Your order has already been dispatched and can no longer be cancelled. "
                        "Once it arrives, you can initiate a return through this chat."
                    )
                elif status_lower == "created":
                    tip = (
                        "Your order is still being set up and cannot be cancelled yet. "
                        "Please try again in a few minutes once it moves to processing."
                    )
                elif status_lower == "approved":
                    tip = (
                        "Your order has been approved for processing and cannot be cancelled "
                        "at this stage. Please wait until it moves to processing or contact "
                        "our support team for assistance."
                    )
                else:
                    tip = (
                        f"Orders with status '{raw_status}' cannot be cancelled. "
                        "Please contact our support team if you need further help."
                    )

                return self.success({
                    "outcome":      "not_cancellable",
                    "order_id":     order_id,
                    "order_status": raw_status,
                    "message":      tip,
                })

        except Exception as e:
            logger.exception(f"CancelOrderPG.execute failed — order_id={order_id}")
            return self.error(f"Unexpected error while processing cancellation: {str(e)}")

# ── 9. Escalate to Human ─────────────────────────────────────────────────────

class EscalateToHumanPG(BaseTool):
    def __init__(self, session_factory):
        self._session_factory = session_factory

    @property
    def name(self) -> str:
        return "escalate_to_human"

    @property
    def description(self) -> str:
        return (
            "Escalate a customer issue to a human agent when it cannot be resolved automatically. "
            "Use when: customer requests human/manager, billing dispute, account suspended, "
            "legal threat, chargeback mention, order >$500 dispute, damage outside return window, "
            "delivered but not received after 24h, refund not received after 10 business days, "
            "or customer has contacted support 3+ times about same issue. "
            "DO NOT use for issues the agent can resolve with available tools."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "Customer email"
                },
                "reason": {
                    "type": "string",
                    "enum": [
                        "delivered_not_received",
                        "refund_not_received",
                        "damage_outside_return_window",
                        "account_suspended",
                        "data_request",
                        "legal_threat",
                        "high_value_dispute",
                        "repeated_contact",
                        "customer_requested",
                        "other"
                    ],
                    "description": "Reason for escalation"
                },
                "order_id": {
                    "type": "string",
                    "description": "Related order ID if applicable"
                },
                "customer_note": {
                    "type": "string",
                    "description": "Brief summary of what the customer said"
                },
            },
            "required": ["email", "reason", "customer_note"]
        }

    async def execute(self, **kwargs: Any) -> dict:
        email         = kwargs.get("email", "").strip().lower()
        reason        = kwargs.get("reason", "").strip()
        order_id      = kwargs.get("order_id", "").strip()
        customer_note = kwargs.get("customer_note", "").strip()

        if not email:
            return self.error("email is required.")
        if not reason:
            return self.error("reason is required.")
        if not customer_note:
            return self.error("customer_note is required.")

        try:
            async with self._session_factory() as session:

                # ── 1. Verify user + get loyalty tier ────────────────────────
                user_result = await session.execute(
                    text("""
                        SELECT id, loyalty_tier
                        FROM users
                        WHERE LOWER(email) = :email
                    """),
                    {"email": email}
                )
                user = user_result.mappings().first()
                if not user:
                    return self.error(f"No account found for email: {email}")

                # ── 2. Check for existing open escalation ────────────────────
                existing_result = await session.execute(
                    text("""
                        SELECT id FROM escalations
                        WHERE user_id = :user_id
                          AND status  = 'open'
                          AND reason  = :reason
                        LIMIT 1
                    """),
                    {"user_id": user["id"], "reason": reason}
                )
                existing = existing_result.mappings().first()
                if existing:
                    return self.success({
                        "outcome":      "already_open",
                        "escalation_id": existing["id"],
                        "message": (
                            "There is already an open escalation for this issue. "
                            "Our team will contact you within 1–2 business days."
                        ),
                    })

                # ── 3. Determine priority (Platinum tier) ────────────────────
                is_priority = user["loyalty_tier"] == "Platinum"

                # ── 4. Also flag legal threats and high value as priority ─────
                if reason in ("legal_threat", "high_value_dispute"):
                    is_priority = True

                # ── 5. Insert escalation ─────────────────────────────────────
                escalation_id = str(uuid.uuid4())
                now           = datetime.now(timezone.utc)

                note = customer_note
                if is_priority:
                    note = f"[PRIORITY] {note}"

                await session.execute(
                    text("""
                        INSERT INTO escalations (
                            id, user_id, session_id, order_id,
                            reason, customer_note, priority,
                            status, created_at
                        ) VALUES (
                            :id, :user_id, NULL, :order_id,
                            :reason, :customer_note, :priority,
                            'open', :created_at
                        )
                    """),
                    {
                        "id":            escalation_id,
                        "user_id":       user["id"],
                        "order_id":      order_id or None,
                        "reason":        reason,
                        "customer_note": note,
                        "priority":      is_priority,
                        "created_at":    now,
                    }
                )
                await session.commit()

                # ── 6. Broadcast to admin CRM ────────────────────────────────
                try:
                    from backend.api.websocket import ws_manager
                    await ws_manager.broadcast_to_admins({
                        "type":          "new_escalation",
                        "escalation_id": escalation_id,
                        "order_id":      order_id or None,
                        "reason":        reason,
                        "priority":      is_priority,
                    })
                except Exception as broadcast_err:
                    logger.warning(f"Admin broadcast failed: {broadcast_err}")

                # ── 7. Build response based on tier ──────────────────────────
                sla = "by end of next business day" if is_priority else "within 1–2 business days"

                return self.success({
                    "outcome":      "escalated",
                    "escalation_id": escalation_id,
                    "priority":     is_priority,
                    "message": (
                        f"I want to make sure this gets proper attention. "
                        f"I'm flagging this for our team — you'll hear back "
                        f"at {email} {sla}. "
                        f"Keep your order ID handy: {order_id or 'N/A'}."
                    ),
                    "sla": sla,
                })

        except Exception as e:
            logger.exception(f"escalate_to_human failed for {email}")
            return self.error(f"Failed to create escalation: {str(e)}")
        
class ReorderLastOrderPG(BaseTool):
    """Recreates customer's last delivered order with current prices and stock validation."""
 
    def __init__(self, session_factory):
        self._session_factory = session_factory
 
    @property
    def name(self) -> str:
        return "reorder_last_order"
 
    @property
    def description(self) -> str:
        return (
            "Recreate customer's most recent delivered/completed order as new Processing order. "
            "Triggers: repeat order, reorder, buy again, same items again. "
            "Validates stock, uses current prices, decrements inventory."
        )
 
    @property
    def parameters(self) -> dict:
        return {
        "type": "object",
        "properties": {
            "email":    {"type": "string", "description": "Customer email"},
            "order_id": {"type": "string", "description": "Specific order ID to reorder (optional — omit to use last delivered order)"},
        },
        "required": ["email"]
        }
 
    async def execute(self, **kwargs: Any) -> dict:
        email    = kwargs.get("email", "").strip().lower()
        order_id = kwargs.get("order_id", "").strip() or None  # optional — user can specify which order to reorder
        if not email:
            return self.error("email required")

        try:
            async with self._session_factory() as session:

                # Verify customer and get address
                result = await session.execute(
                    text("""
                        SELECT c.customer_id, c.full_address
                        FROM customers c
                        JOIN users u ON LOWER(c.email) = LOWER(u.email)
                        WHERE LOWER(c.email) = :email
                        LIMIT 1
                    """),
                    {"email": email}
                )
                customer = result.mappings().first()
                if not customer:
                    return self.error(f"No account found: {email}")

                customer_id = customer["customer_id"]

                # Get source order — use specified order_id if provided, else last delivered
                if order_id:
                    order_result = await session.execute(
                        text("""
                            SELECT order_id
                            FROM orders
                            WHERE customer_id = :cid
                            AND order_id = :oid
                            AND LOWER(order_status) IN ('delivered', 'completed')
                            LIMIT 1
                        """),
                        {"cid": customer_id, "oid": order_id}
                    )
                else:
                    order_result = await session.execute(
                        text("""
                            SELECT order_id
                            FROM orders
                            WHERE customer_id = :cid
                            AND LOWER(order_status) IN ('delivered', 'completed')
                            ORDER BY order_purchase_timestamp DESC
                            LIMIT 1
                        """),
                        {"cid": customer_id}
                    )

                src = order_result.mappings().first()
                if not src:
                    return self.success({
                        "outcome": "no_orders",
                        "message": "No completed orders found to reorder."
                    })

                src_id = src["order_id"]

                # Get items from order_items (use order item price, not product price)
                items_result = await session.execute(
                    text("""
                        SELECT oi.product_id, oi.seller_id,
                            oi.price, oi.freight_value,
                            p.product_name, p.stock_quantity
                        FROM order_items oi
                        JOIN products p ON oi.product_id = p.product_id
                        WHERE oi.order_id = :oid
                        AND p.stock_quantity > 0
                    """),
                    {"oid": src_id}
                )
                items = items_result.mappings().all()

                if not items:
                    return self.success({
                        "outcome": "unavailable",
                        "message": "Items from your previous order are out of stock."
                    })

                # Calculate totals using order_items price + freight (not product_price)
                total = sum(float(i["price"]) + float(i["freight_value"] or 0) for i in items)

                # Create new order
                new_id = str(uuid.uuid4())
                now    = datetime.utcnow()
                eta    = now + timedelta(days=7)

                await session.execute(
                    text("""
                        INSERT INTO orders (
                            order_id, customer_id, order_status,
                            delivery_full_address,
                            order_purchase_timestamp, order_estimated_delivery_date,
                            reorder_source_id
                        ) VALUES (
                            :oid, :cid, 'Processing',
                            :addr, :ts, :eta, :src
                        )
                    """),
                    {
                        "oid": new_id, "cid": customer_id,
                        "addr": customer["full_address"],
                        "ts": now, "eta": eta, "src": src_id
                    }
                )

                # Insert items + decrement stock
                for item in items:
                    max_id_result = await session.execute(
                        text("SELECT COALESCE(MAX(order_item_id), 0) FROM order_items")
                    )
                    next_item_id = max_id_result.scalar() + 1

                    await session.execute(
                        text("""
                            INSERT INTO order_items (
                                order_item_id, order_id, product_id, seller_id,
                                price, freight_value
                            ) VALUES (
                                :iid, :oid, :pid, :sid, :price, :freight
                            )
                        """),
                        {
                            "iid":    next_item_id,
                            "oid":    new_id,
                            "pid":    item["product_id"],
                            "sid":    item["seller_id"],
                            "price":  item["price"],          # from order_items, not products
                            "freight": item["freight_value"],
                        }
                    )

                    await session.execute(
                        text("UPDATE products SET stock_quantity = stock_quantity - 1 WHERE product_id = :pid"),
                        {"pid": item["product_id"]}
                    )

                # Always use cash_on_delivery for reorders
                await session.execute(
                    text("""
                        INSERT INTO order_payments (order_id, payment_type, payment_value)
                        VALUES (:oid, :type, :val)
                    """),
                    {"oid": new_id, "type": "cash_on_delivery", "val": round(total, 2)}
                )

                await session.commit()

                logger.info(f"[REORDER] {new_id} from {src_id} for customer {customer_id}")

                return self.success({
                    "outcome":      "reordered",
                    "new_order_id": new_id,
                    "source_order_id": src_id,
                    "items":        [i["product_name"] for i in items],
                    "total_items":  len(items),
                    "total":        round(total, 2),
                    "payment_method": "cash_on_delivery",
                    "eta":          eta.strftime("%B %d, %Y"),
                    "message": (
                        f"Reorder placed successfully! Order ID: {new_id}. "
                        f"Payment: Cash on Delivery. Total: ₹{round(total, 2)}. "
                        f"ETA: {eta.strftime('%B %d, %Y')}."
                    )
                })

        except Exception as e:
            logger.exception(f"ReorderLastOrderPG failed: {email}")
            return self.error(f"Reorder failed: {str(e)}")

# ── Tool registry ─────────────────────────────────────────────────────────────

def get_all_pg_tools(session_factory) -> list[BaseTool]:
    return [
        ThinkTool(),
        GetOrderHistoryPG(session_factory),
        GetOrderDetailsPG(session_factory),
        GetOrderStatusPG(session_factory),
        ChangeDeliveryDatePG(session_factory),
        ChangeDeliveryAddressPG(session_factory),
        GetPaymentInfoPG(session_factory),
        GetSellerInfoPG(session_factory),
        GetUserProfilePG(session_factory),
        UpdateUserProfilePG(session_factory),
        InitiateReturnPG(session_factory),
        ReportMissingItemPG(session_factory),
        GetRequestStatusPG(session_factory),
        CancelOrderPG(session_factory),
        EscalateToHumanPG(session_factory),
        ReorderLastOrderPG(session_factory),
    ]