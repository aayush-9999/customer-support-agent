# backend/tools/pg_tools.py

import logging
from typing import Any
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
import uuid
from backend.tools.base import BaseTool

logger = logging.getLogger(__name__)

# ── 0. Think Tool ───────────────────────────────────────────────────────────────
# A no-op tool that forces the model to externalise its reasoning before
# calling any data-fetching tool. Costs almost nothing (result is just
# {"ok": true}) but prevents the most common agent mistakes on smaller models:
# Calling get_order_details without a confirmed ID
# Calling change_delivery_date without reading warehouse date first
# Making a tool call when the answer is already in history
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
    
# ── 1. Get Order History (list) ─────────────────────────────────────────────

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
                })

        except Exception as e:
            logger.exception(f"get_order_history failed for {email}")
            return self.error(f"Failed to retrieve order history: {str(e)}")


# ── 2. Get Order Details (single, full) ─────────────────────────────────────

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

                # ── 1. Verify user exists in users table ─────────────────
                user_result = await session.execute(
                    text("SELECT id, email FROM users WHERE LOWER(email) = :email"),
                    {"email": email}
                )
                if not user_result.mappings().first():
                    return self.error(f"No account found for email: {email}")

                # ── 2. Find matching customer record by same email ────────
                customer_result = await session.execute(
                    text("SELECT customer_id FROM customers WHERE LOWER(email) = :email"),
                    {"email": email}
                )
                customer = customer_result.mappings().first()
                if not customer:
                    return self.error("No order history found for this account.")

                customer_id = customer["customer_id"]

                # ── 3. If no order_id, find the latest order_id first ────
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

                # ── 4. Fetch full details for the resolved order_id ──────
                # Aggregate payments per order to avoid row duplication
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

                # ── 5. Build response ────────────────────────────────────
                first = rows[0]
                order = {
                    "email":        email,
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
        

# ── 2. Get Order Status ─────────────────────────────────────────────────────────

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

                # ── 1. Verify user exists ────────────────────────────────
                user_result = await session.execute(
                    text("SELECT id FROM users WHERE LOWER(email) = :email"),
                    {"email": email}
                )
                if not user_result.mappings().first():
                    return self.error(f"No account found for email: {email}")

                # ── 2. Resolve customer_id ───────────────────────────────
                customer_result = await session.execute(
                    text("SELECT customer_id FROM customers WHERE LOWER(email) = :email"),
                    {"email": email}
                )
                customer = customer_result.mappings().first()
                if not customer:
                    return self.error("No order history found for this account.")

                customer_id = customer["customer_id"]

                # ── 3. If no order_id, use latest ────────────────────────
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

                # ── 4. Fetch status — with ownership check ───────────────
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
                    "email":        email,
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

# ── 4. Change Delivery Date ─────────────────────────────────────────────────

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
 
        if not email:
            return self.error("email is required.")
        if not order_id:
            return self.error("order_id is required.")
        if not requested_date:
            return self.error("requested_date is required.")
 
        try:
            req_dt = datetime.strptime(requested_date, "%Y-%m-%d")
        except ValueError:
            return self.error(
                f"Invalid date format '{requested_date}'. Please use YYYY-MM-DD."
            )
 
        if req_dt.date() <= datetime.utcnow().date():
            return self.error("Requested date must be in the future.")
 
        try:
            async with self._session_factory() as session:
 
                # ── 1. Verify user ───────────────────────────────────────
                user_result = await session.execute(
                    text("SELECT id FROM users WHERE LOWER(email) = :email"),
                    {"email": email}
                )
                user = user_result.mappings().first()
                if not user:
                    return self.error(f"No account found for email: {email}")
 
                user_id = user["id"]
 
                # ── 2. Verify order ownership ────────────────────────────
                customer_result = await session.execute(
                    text("SELECT customer_id FROM customers WHERE LOWER(email) = :email"),
                    {"email": email}
                )
                customer = customer_result.mappings().first()
                if not customer:
                    return self.error("No order history found for this account.")
 
                order_result = await session.execute(
                    text("""
                        SELECT order_id, order_status, order_estimated_delivery_date
                        FROM orders
                        WHERE order_id    = :order_id
                          AND customer_id = :customer_id
                    """),
                    {"order_id": order_id, "customer_id": customer["customer_id"]}
                )
                order = order_result.mappings().first()
                if not order:
                    return self.error(f"No order found with ID {order_id}.")
 
                # ── 3. Check terminal states ─────────────────────────────
                status = order["order_status"]
                if status in ("delivered", "cancelled", "shipped"):
                    return self.success({
                        "outcome": "rejected",
                        "reason": (
                            "Your order has already been shipped and the delivery address "
                            "cannot be changed at this stage. If the package is returned to us, "
                            "we will reship it to your correct address at no charge."
                        ),
                        "email":    email,
                        "order_id": order_id,
                    })
 
                # ── 4. Check for existing pending request ────────────────
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
                            "Our team is reviewing it and will confirm within 24 hours. "
                            "Please wait for that confirmation before submitting a new request."
                        ),
                        "existing_requested_date": str(existing["requested_date"]),
                        "request_id":              existing["id"],
                        "email":                   email,
                        "order_id":                order_id,
                    })
 
                # ── 5. Insert pending request (session_id left NULL here) ─
                #       routes.py will backfill it immediately after the agent
                #       returns, using the route's own injected pg_session.
                now        = datetime.utcnow()
                request_id = str(uuid.uuid4())
 
                await session.execute(
                    text("""
                        INSERT INTO pending_requests
                            (id, type, status, order_id, user_id,
                             requested_date, "current_date", session_id, created_at)
                        VALUES
                            (:id, :type, :status, :order_id, :user_id,
                             :requested_date, :current_date, NULL, :created_at)
                    """),
                    {
                        "id":             request_id,
                        "type":           "date_change",
                        "status":         "pending",
                        "order_id":       order_id,
                        "user_id":        user_id,
                        "requested_date": req_dt,
                        "current_date":   order["order_estimated_delivery_date"],
                        "created_at":     now,
                    }
                )
                await session.commit()
 
                # ── 6. Broadcast to admin CRM ────────────────────────────
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
            "pincode": {"type": "string", "description": "New zip/pincode"},
        },
            "required": ["email", "order_id", "full_address", "city", "state", "pincode"]
        }

    async def execute(self, **kwargs: Any) -> dict:
        email        = kwargs.get("email", "").strip().lower()
        order_id     = kwargs.get("order_id", "").strip()
        full_address = kwargs.get("full_address", "").strip()
        city         = kwargs.get("city", "").strip()
        state        = kwargs.get("state", "").strip()
        pincode = kwargs.get("pincode", "").strip()

        # ── Input validation ─────────────────────────────────────────────────
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

                # ── 3. Check terminal states ─────────────────────────────────
                status = order["order_status"]
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
                # ── 4. PROCESSING → direct update ────────────────────────────
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

                    # Notify customer via WS (session_id backfilled by routes.py)
                    # We broadcast to admins too for visibility
                    try:
                        from backend.api.websocket import ws_manager
                        await ws_manager.broadcast_to_admins({
                            "type":     "address_updated_directly",
                            "order_id": order_id,
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

                # ── 5. Any other status (e.g. 'invoiced') ───────────────────
                return self.error(
                    f"Address cannot be changed for an order with status '{status}'."
                )

        except Exception as e:
            logger.exception(f"change_delivery_address failed for {order_id}")
            return self.error(f"Failed to process address change: {str(e)}")
 
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
 
                # ── 1. Verify user account exists ────────────────────────
                user_row = await session.execute(
                    text("SELECT id FROM users WHERE LOWER(email) = :email"),
                    {"email": email}
                )
                if not user_row.mappings().first():
                    return self.error(f"No account found for email: {email}")
 
                # ── 2. Resolve customer_id (order data lives here) ───────
                cust_row = await session.execute(
                    text("SELECT customer_id FROM customers WHERE LOWER(email) = :email"),
                    {"email": email}
                )
                customer = cust_row.mappings().first()
                if not customer:
                    return self.error("No order history found for this account.")
 
                customer_id = customer["customer_id"]
 
                # ── 3. Resolve order_id — default to latest ──────────────
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
 
                # ── 4. Ownership check + basic order info ────────────────
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
                    return self.error(
                        f"No order found with ID '{order_id}' for this account."
                    )
 
                # ── 5. Fetch all payment rows for the order ───────────────
                pay_rows = await session.execute(
                    text("""
                        SELECT
                            payment_type,
                            payment_value
                        FROM  order_payments
                        WHERE order_id = :order_id
                    """),
                    {"order_id": order_id}
                )
                payments = pay_rows.mappings().all()
 
                if not payments:
                    return self.error(
                        f"No payment records found for order '{order_id}'."
                    )
 
                # ── 6. Aggregate summary ─────────────────────────────────
                total_paid    = sum(float(p["payment_value"]) for p in payments)
                payment_types = list({p["payment_type"] for p in payments})
 
                # Per-method subtotals
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
                    "email":           email,    
                    "order_id":        order_id,
                    "is_latest_order": is_latest,
                    "order_status":    order["order_status"],
                    "ordered_at":      str(order["order_purchase_timestamp"])
                                       if order["order_purchase_timestamp"] else None,
                    "estimated_delivery": str(order["order_estimated_delivery_date"])
                                          if order["order_estimated_delivery_date"] else None,
                    # ── Payment summary ──────────────────────────────────
                    "total_paid":      round(total_paid, 2),
                    "payment_methods": payment_types,   # e.g. ["credit_card", "voucher"]
                    "method_totals":   method_totals,   # e.g. {"credit_card": 120.0}
                    "transactions":    breakdown,        # full line-by-line
                    "message": (
                        "Here are the payment details for your "
                        + ("most recent order." if is_latest else f"order {order_id}.")
                    ),
                })
 
        except Exception as e:
            logger.exception(
                f"get_payment_info failed for email={email}, order_id={order_id}"
            )
            return self.error(f"Failed to retrieve payment info: {str(e)}")
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
                "email": {"type": "string", "description": "Customer's email address."},
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
                                "seller_id":   row["seller_id"],
                                "shop_name":   row["shop_name"],
                                "phone":       row["phone"],
                                "email":       row["seller_email"],
                                "city":        row["seller_city"],
                                "state":       row["seller_state"],
                                "address":     row["full_address"],
                                "pincode":     row["pincode"],
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
                    "account_status": profile["account_status"],   # "active" or "deactive"
                    "is_active":      profile["is_active"],
                    "loyalty_tier":   profile["loyalty_tier"],
                    "loyalty_points": profile["loyalty_points"],
                    "member_since":   str(profile["created_at"]) if profile["created_at"] else None,
                })

        except Exception as e:
            logger.exception(f"get_user_profile failed for email={email}")
            return self.error(f"Failed to retrieve profile: {str(e)}")

def get_all_pg_tools(session_factory) -> list[BaseTool]:
    return [
        ThinkTool(),  # always include the ThinkTool for better agent reasoning
        GetOrderHistoryPG(session_factory),
        GetOrderDetailsPG(session_factory),
        GetOrderStatusPG(session_factory),
        ChangeDeliveryDatePG(session_factory),
        GetPaymentInfoPG(session_factory),
        GetSellerInfoPG(session_factory),
        GetUserProfilePG(session_factory),  # ← add
    ]