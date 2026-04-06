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
            "Returns all orders with product names, quantities, prices, "
            "payment method, order status, and delivery dates. "
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

                # ── 1. Resolve customer ──────────────────────────────────
                customer = await session.execute(
                    text("SELECT customer_id, email FROM customers WHERE LOWER(email) = :email"),
                    {"email": email}
                )
                customer = customer.mappings().first()

                if not customer:
                    return self.error(f"No account found for email: {email}")

                customer_id = customer["customer_id"]

                # ── 2. Build query — single order or all orders ──────────
                base_filter = "o.customer_id = :customer_id"
                params      = {"customer_id": customer_id}

                if order_id:
                    base_filter += " AND o.order_id = :order_id"
                    params["order_id"] = order_id

                query = text(f"""
                    SELECT
                        o.order_id,
                        o.order_status,
                        o.order_purchase_timestamp,
                        o.order_estimated_delivery_date,
                        o.order_delivered_customer_date,

                        -- line items
                        oi.order_item_id,
                        oi.price,
                        oi.freight_value,

                        -- product
                        p.product_name,
                        p.product_category_name,

                        -- payment
                        op.payment_type,
                        op.payment_value

                    FROM orders o
                    JOIN order_items   oi ON oi.order_id   = o.order_id
                    JOIN products      p  ON p.product_id  = oi.product_id
                    LEFT JOIN order_payments op ON op.order_id = o.order_id

                    WHERE {base_filter}
                    ORDER BY o.order_purchase_timestamp DESC
                """)

                rows = await session.execute(query, params)
                rows = rows.mappings().all()

                if not rows:
                    return self.success({
                        "orders": [],
                        "message": "No orders found for this account."
                    })

                # ── 3. Group rows by order_id ────────────────────────────
                orders_map: dict[str, dict] = {}

                for row in rows:
                    oid = row["order_id"]

                    if oid not in orders_map:
                        orders_map[oid] = {
                            "order_id":          oid,
                            "status":            row["order_status"],
                            "placed_at":         str(row["order_purchase_timestamp"]) if row["order_purchase_timestamp"] else None,
                            "estimated_delivery": str(row["order_estimated_delivery_date"]) if row["order_estimated_delivery_date"] else None,
                            "delivered_at":      str(row["order_delivered_customer_date"]) if row["order_delivered_customer_date"] else None,
                            "payment_type":      row["payment_type"],
                            "total_paid":        float(row["payment_value"]) if row["payment_value"] else None,
                            "items":             [],
                        }

                    orders_map[oid]["items"].append({
                        "product_name":     row["product_name"],
                        "category":         row["product_category_name"],
                        "price":            float(row["price"]),
                        "freight":          float(row["freight_value"]),
                        "item_total":       float(row["price"] + row["freight_value"]),
                    })

                orders = list(orders_map.values())

                return self.success({
                    "total_orders": len(orders),
                    "orders":       orders,
                })

        except Exception as e:
            logger.exception(f"get_order_details_pg failed for {email}")
            return self.error(f"Failed to retrieve orders: {str(e)}")
def get_all_pg_tools(session_factory) -> list[BaseTool]:
        return [
        GetOrderDetailsPG(session_factory),
    ]