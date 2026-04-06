# backend/tools/pg_tools.py

import logging
from typing import Any
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.tools.base import BaseTool

logger = logging.getLogger(__name__)


class GetOrderDetailsPG(BaseTool):
    def __init__(self, session_factory):
        self._session_factory = session_factory

    @property
    def name(self) -> str:
        return "get_order_details_pg"

    @property
    def description(self) -> str:
        return (
            "Retrieve what a customer ordered using their email address. "
            "Returns the latest order (or a specific order) with product names, "
            "quantities, prices, payment method, order status, and delivery dates. "
            "Use when the customer asks 'what did I order', 'my orders', "
            "'what did I buy', 'show my purchases', or asks about a specific order."
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
                    "description": "Specific order ID if customer is asking about one order (optional)"
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
        return "get_order_status_pg"

    @property
    def description(self) -> str:
        return (
            "Get the current status of a specific order using the order ID. "
            "Returns the status, a plain-language explanation, order date, "
            "estimated delivery, and a delay flag if the order is overdue. "
            "Use when the customer asks 'where is my order', 'has my order shipped', "
            "or any question about the current state of an order."
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


# ── Registry ────────────────────────────────────────────────────────────────────

def get_all_pg_tools(session_factory) -> list[BaseTool]:
    return [
        GetOrderDetailsPG(session_factory),
        GetOrderStatusPG(session_factory),
    ]