# backend/tools/pg_tools.py

import logging
from typing import Any
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
import uuid
from backend.tools.base import BaseTool

logger = logging.getLogger(__name__)

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
            "Request a change to the estimated delivery date of an order. "
            "Creates a pending request for admin approval. "
            "Use when the customer asks to change, reschedule, or delay their delivery. "
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
                if status in ("delivered", "cancelled"):
                    return self.success({
                        "outcome":  "rejected",
                        "reason":   (
                            f"Your order is already '{status}' — "
                            "the delivery date cannot be changed at this stage."
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
 

def get_all_pg_tools(session_factory) -> list[BaseTool]:
    return [
        GetOrderHistoryPG(session_factory),
        GetOrderDetailsPG(session_factory),
        GetOrderStatusPG(session_factory),
        ChangeDeliveryDatePG(session_factory),
        GetPaymentInfoPG(session_factory),
    ]
 