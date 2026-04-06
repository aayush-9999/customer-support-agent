# backend/tools/mongo_tools.py
# Diff from original: ChangeDeliveryDate.execute() now calls
# ws_manager.broadcast_to_admins() after inserting a pending_request,
# so the CRM gets a push instead of waiting for a 10s poll.

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.tools.base import BaseTool

logger = logging.getLogger(__name__)


def _serialize(doc: dict) -> dict:
    """Convert MongoDB doc to JSON-serializable dict."""
    if doc is None:
        return {}
    result = {}
    for k, v in doc.items():
        if k == "_id":
            result[k] = str(v)
        elif isinstance(v, datetime):
            result[k] = v.isoformat()
        elif isinstance(v, ObjectId):
            result[k] = str(v)
        elif isinstance(v, dict):
            result[k] = _serialize(v)
        elif isinstance(v, list):
            result[k] = [
                _serialize(i) if isinstance(i, dict) else str(i) if isinstance(i, ObjectId) else i
                for i in v
            ]
        else:
            result[k] = v
    return result


# ── 1. Get Order Details ────────────────────────────────────────────────────────

class GetOrderDetails(BaseTool):
    def __init__(self, db: AsyncIOMotorDatabase):
        self._db = db

    @property
    def name(self) -> str:
        return "get_order_details"

    @property
    def description(self) -> str:
        return (
            "Retrieve FULL details of a SPECIFIC order by its order_id. "
            "Returns order status, products, shipping address, estimated dates, and status history. "
            "IMPORTANT: Only call this tool AFTER the customer has confirmed which order they mean. "
            "If the customer has not specified an order, call get_order_history first to list their "
            "orders, then ask them to pick one. Never call this with a guessed or assumed order_id."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": (
                        "The MongoDB order ID confirmed by the customer "
                        "(e.g. 682b73a0463e7f2b09ed2b1a). "
                        "Must come from get_order_history results — never guessed."
                    )
                }
            },
            "required": ["order_id"]
        }

    async def execute(self, **kwargs: Any) -> dict:
        order_id = kwargs.get("order_id", "").strip()
        if not order_id:
            return self.error("order_id is required.")

        try:
            oid = ObjectId(order_id)
        except Exception:
            return self.error(f"'{order_id}' is not a valid order ID format.")

        try:
            order = await self._db.orders.find_one({"_id": oid})
            if not order:
                return self.error(f"No order found with ID {order_id}.")

            # Enrich with invoice if available
            invoice = None
            if order.get("invoiceId"):
                try:
                    inv_id = ObjectId(str(order["invoiceId"]))
                    invoice = await self._db.invoices.find_one({"_id": inv_id})
                except Exception:
                    pass

            data = _serialize(order)

            # Attach payment summary from invoice if found
            if invoice:
                erp = invoice.get("metadata", {}).get("erpDetails", {})
                data["payment_summary"] = {
                    "total_amount": invoice.get("totalAmount"),
                    "status": invoice.get("status"),
                    "due_date": erp.get("dueDate"),
                }

            return self.success(data)

        except Exception as e:
            logger.exception(f"get_order_details failed for {order_id}")
            return self.error(f"Failed to retrieve order: {str(e)}")


# ── 2. Get User Profile ─────────────────────────────────────────────────────────

class GetUserProfile(BaseTool):
    def __init__(self, db: AsyncIOMotorDatabase):
        self._db = db

    @property
    def name(self) -> str:
        return "get_user_profile"

    @property
    def description(self) -> str:
        return (
            "Retrieve a customer's profile by their email address. "
            "Returns name, account status, loyalty tier, loyalty points, "
            "and contact details. Use when the customer asks about their account."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "The customer's email address"
                }
            },
            "required": ["email"]
        }

    async def execute(self, **kwargs: Any) -> dict:
        email = kwargs.get("email", "").strip().lower()
        if not email:
            return self.error("email is required.")

        try:
            user = await self._db.users.find_one(
                {"email": {"$regex": f"^{email}$", "$options": "i"}},
                {"lastRecommendations": 0, "vai_text_embedding": 0}
            )
            if not user:
                return self.error(f"No account found for email: {email}")

            return self.success(_serialize(user))

        except Exception as e:
            logger.exception(f"get_user_profile failed for {email}")
            return self.error(f"Failed to retrieve profile: {str(e)}")


# ── 3. Get Order History ────────────────────────────────────────────────────────

class GetOrderHistory(BaseTool):
    def __init__(self, db: AsyncIOMotorDatabase):
        self._db = db

    @property
    def name(self) -> str:
        return "get_order_history"

    @property
    def description(self) -> str:
        return (
            "Retrieve all recent orders for a customer by email. "
            "Returns a summary list with order_id, status, item names, and dates. "
            "ALWAYS call this tool FIRST whenever a customer asks about 'my order', "
            "'where is my package', 'my delivery', or any order-related question "
            "where they have not specified a particular order. "
            "After getting results: if there is only 1 order, proceed with it. "
            "If there are multiple orders, show the customer a plain-language list "
            "and ask which one they mean — then call get_order_details on the chosen one. "
            "Active orders (Processing, Shipped, In Transit) should be surfaced first."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "The customer's email address"
                }
            },
            "required": ["email"]
        }

    async def execute(self, **kwargs: Any) -> dict:
        email = kwargs.get("email", "").strip().lower()
        if not email:
            return self.error("email is required.")

        try:
            user = await self._db.users.find_one(
                {"email": {"$regex": f"^{email}$", "$options": "i"}},
                {"_id": 1}
            )
            if not user:
                return self.error(f"No account found for email: {email}")

            cursor = self._db.orders.find(
                {"userId": user["_id"]},
                {
                    "_id": 1,
                    "status": 1,
                    "createdAt": 1,
                    "estimated_destination_date": 1,
                    "products": 1,
                }
            ).sort("createdAt", -1).limit(10)

            # Separate active vs completed orders so agent can surface active first
            active_statuses = {"Processing", "In process", "Shipped", "In Transit",
                               "Out for Delivery", "Ready for delivery", "Delayed"}

            active_orders = []
            other_orders = []

            async for order in cursor:
                products = order.get("products", [])
                status = order.get("status", "Unknown")
                entry = {
                    "order_id": str(order["_id"]),
                    "status": status,
                    "is_active": status in active_statuses,
                    "created_at": (
                        order["createdAt"].isoformat()
                        if isinstance(order.get("createdAt"), datetime)
                        else str(order.get("createdAt"))
                    ),
                    "estimated_delivery": (
                        order["estimated_destination_date"].isoformat()
                        if isinstance(order.get("estimated_destination_date"), datetime)
                        else None
                    ),
                    "item_count": len(products),
                    "items": [p.get("name", "Unknown") for p in products[:3]],
                }
                if status in active_statuses:
                    active_orders.append(entry)
                else:
                    other_orders.append(entry)

            # Return active orders first, then the rest
            orders = active_orders + other_orders

            if not orders:
                return self.success({
                    "orders": [],
                    "total": 0,
                    "active_count": 0,
                    "message": "No orders found for this account."
                })

            return self.success({
                "orders": orders,
                "total": len(orders),
                "active_count": len(active_orders),
            })

        except Exception as e:
            logger.exception(f"get_order_history failed for {email}")
            return self.error(f"Failed to retrieve order history: {str(e)}")


# ── 4. Get Return Status ────────────────────────────────────────────────────────

class GetReturnStatus(BaseTool):
    def __init__(self, db: AsyncIOMotorDatabase):
        self._db = db

    @property
    def name(self) -> str:
        return "get_return_status"

    @property
    def description(self) -> str:
        return (
            "Retrieve the return/refund status for a specific order. "
            "Returns return status, items being returned, refund amount, and timeline. "
            "Use when the customer asks about a return or refund. "
            "If they haven't specified which order, call get_order_history first."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The order ID to look up the return for"
                }
            },
            "required": ["order_id"]
        }

    async def execute(self, **kwargs: Any) -> dict:
        order_id = kwargs.get("order_id", "").strip()
        if not order_id:
            return self.error("order_id is required.")

        try:
            oid = ObjectId(order_id)
        except Exception:
            return self.error(f"'{order_id}' is not a valid order ID format.")

        try:
            ret = await self._db.returns.find_one({"orderId": oid})
            if not ret:
                return self.error(f"No return found for order {order_id}.")

            return self.success(_serialize(ret))

        except Exception as e:
            logger.exception(f"get_return_status failed for {order_id}")
            return self.error(f"Failed to retrieve return status: {str(e)}")


# ── 5. Change Delivery Date ─────────────────────────────────────────────────────

class ChangeDeliveryDate(BaseTool):
    def __init__(self, db: AsyncIOMotorDatabase):
        self._db = db

    @property
    def name(self) -> str:
        return "change_delivery_date"

    @property
    def description(self) -> str:
        return (
            "Request a change to the estimated delivery date of an order. "
            "Automatically approves or creates a pending request based on warehouse schedule. "
            "Use when the customer asks to change, reschedule, or delay their delivery. "
            "If the customer hasn't specified which order, call get_order_history first "
            "and confirm the order before calling this tool."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The order ID confirmed by the customer"
                },
                "requested_date": {
                    "type": "string",
                    "description": "Requested new delivery date in YYYY-MM-DD format"
                }
            },
            "required": ["order_id", "requested_date"]
        }

    async def execute(self, **kwargs: Any) -> dict:
        order_id       = kwargs.get("order_id", "").strip()
        requested_date = kwargs.get("requested_date", "").strip()

        if not order_id or not requested_date:
            return self.error("Both order_id and requested_date are required.")

        try:
            req_dt = datetime.strptime(requested_date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return self.error(
                f"Invalid date format '{requested_date}'. Please use YYYY-MM-DD."
            )

        try:
            oid = ObjectId(order_id)
        except Exception:
            return self.error(f"'{order_id}' is not a valid order ID.")

        try:
            order = await self._db.orders.find_one(
                {"_id": oid},
                {
                    "status": 1,
                    "userId": 1,
                    "estimated_warehouse_date": 1,
                    "estimated_destination_date": 1,
                    "delivery_date_change_request": 1,
                    "products": 1,
                }
            )
            if not order:
                return self.error(f"No order found with ID {order_id}.")

            status = order.get("status", "")

            # 1. Terminal states
            if status in ("Delivered", "Completed", "Cancelled"):
                return self.success({
                    "outcome": "rejected",
                    "reason": (
                        f"Your order is already '{status}' — "
                        "the delivery date cannot be changed at this stage."
                    ),
                    "order_id": order_id,
                })

            # 2. Get warehouse date
            warehouse_dt = order.get("estimated_warehouse_date")
            if not warehouse_dt:
                return self.error(
                    "Order is missing warehouse date — cannot evaluate request."
                )

            if isinstance(warehouse_dt, datetime) and warehouse_dt.tzinfo is None:
                warehouse_dt = warehouse_dt.replace(tzinfo=timezone.utc)

            # 3. Feasibility check
            if req_dt < warehouse_dt:
                return self.success({
                    "outcome": "rejected",
                    "reason": (
                        f"Your order is estimated to reach our dispatch warehouse on "
                        f"{warehouse_dt.strftime('%B %d, %Y')}. "
                        f"We cannot deliver before that date — "
                        f"the earliest possible delivery is after "
                        f"{warehouse_dt.strftime('%B %d, %Y')}."
                    ),
                    "requested_date": requested_date,
                    "earliest_possible": (
                        warehouse_dt + timedelta(days=1)
                    ).date().isoformat(),
                })

            # 4. Check for existing pending request
            existing = order.get("delivery_date_change_request")
            if existing and existing.get("status") == "pending":
                return self.success({
                    "outcome": "already_pending",
                    "reason": (
                        "There is already a pending date change request for this order. "
                        "Our team is reviewing it and will confirm within 24 hours. "
                        "Please wait for that confirmation before submitting a new request."
                    ),
                    "existing_requested_date": (
                        existing["requested_date"].isoformat()
                        if isinstance(existing.get("requested_date"), datetime)
                        else str(existing.get("requested_date"))
                    ),
                    "request_id": existing.get("request_id"),
                })

            # 5. Feasible + no existing — write to pending_requests collection
            now = datetime.now(timezone.utc)

            pending_doc = {
                "type":               "date_change",
                "status":             "pending",
                "order_id":           oid,
                "user_id":            order.get("userId"),
                "requested_value":    req_dt,
                "current_value":      order.get("estimated_destination_date"),
                "warehouse_date":     warehouse_dt,
                "created_at":         now,
                "resolved_at":        None,
                "resolved_by":        None,
                "resolution_note":    None,
                "session_id":         None,
            }

            result = await self._db.pending_requests.insert_one(pending_doc)
            request_id = str(result.inserted_id)

            # Mirror lightweight reference back to order
            await self._db.orders.update_one(
                {"_id": oid},
                {"$set": {
                    "delivery_date_change_request": {
                        "request_id":     request_id,
                        "status":         "pending",
                        "requested_date": req_dt,
                        "created_at":     now,
                    }
                }}
            )

            # ── FIX: push to all connected CRM admin tabs immediately.
            # Import here (not at module top) to avoid circular imports since
            # ws_manager lives in the api layer.
            try:
                from backend.api.websocket import ws_manager
                await ws_manager.broadcast_to_admins({
                    "type":       "new_request",
                    "request_id": request_id,
                    "order_id":   order_id,
                })
            except Exception as broadcast_err:
                # Never let a failed broadcast block the tool response.
                logger.warning(f"Admin broadcast failed: {broadcast_err}")

            return self.success({
                "outcome":   "pending_approval",
                "request_id": request_id,
                "message": (
                    "Your request is possible based on the current warehouse schedule. "
                    "We've flagged it for our team to confirm. "
                    "You'll hear back within 24 hours."
                ),
                "requested_date":            requested_date,
                "earliest_possible_delivery": warehouse_dt.date().isoformat(),
            })

        except Exception as e:
            logger.exception(f"change_delivery_date failed for {order_id}")
            return self.error(f"Failed to process date change request: {str(e)}")


# ── 6. Change Delivery Address ──────────────────────────────────────────────────

class ChangeDeliveryAddress(BaseTool):
    def __init__(self, db: AsyncIOMotorDatabase):
        self._db = db

    @property
    def name(self) -> str:
        return "change_delivery_address"

    @property
    def description(self) -> str:
        return (
            "Change the delivery address on an order. "
            "Only possible if the order has not yet been shipped. "
            "Use when the customer wants to update where their order is delivered. "
            "If the customer hasn't specified which order, call get_order_history first."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The order ID confirmed by the customer"
                },
                "street_and_number": {
                    "type": "string",
                    "description": "New street address and number"
                },
                "city": {
                    "type": "string",
                    "description": "City"
                },
                "country": {
                    "type": "string",
                    "description": "Country"
                },
                "state": {
                    "type": "string",
                    "description": "State or province (optional)"
                },
                "cp": {
                    "type": "string",
                    "description": "Postal / zip code"
                }
            },
            "required": ["order_id", "street_and_number", "city", "country"]
        }

    async def execute(self, **kwargs: Any) -> dict:
        order_id = kwargs.get("order_id", "").strip()
        if not order_id:
            return self.error("order_id is required.")

        try:
            oid = ObjectId(order_id)
        except Exception:
            return self.error(f"'{order_id}' is not a valid order ID.")

        try:
            order = await self._db.orders.find_one(
                {"_id": oid},
                {"status": 1, "shipping_address": 1}
            )
            if not order:
                return self.error(f"No order found with ID {order_id}.")

            status = order.get("status", "")

            if status not in ("In process", "Ready for delivery"):
                return self.success({
                    "outcome": "rejected",
                    "reason": (
                        f"Your order is currently '{status}'. "
                        "Address changes are only possible before the order is shipped. "
                        "Once an order is picked up from the warehouse, "
                        "we can no longer redirect it."
                    ),
                    "current_status": status,
                })

            new_address = {
                "street_and_number": kwargs.get("street_and_number", "").strip(),
                "city":              kwargs.get("city", "").strip(),
                "country":           kwargs.get("country", "").strip(),
                "state":             kwargs.get("state", "").strip(),
                "cp":                kwargs.get("cp", "").strip(),
            }

            await self._db.orders.update_one(
                {"_id": oid},
                {"$set": {"shipping_address": new_address}}
            )

            return self.success({
                "outcome": "updated",
                "message": "Delivery address successfully updated.",
                "new_address": new_address,
                "order_id": order_id,
            })

        except Exception as e:
            logger.exception(f"change_delivery_address failed for {order_id}")
            return self.error(f"Failed to update address: {str(e)}")


# ── Registry ────────────────────────────────────────────────────────────────────

def get_all_tools(db: AsyncIOMotorDatabase) -> list[BaseTool]:
    return [
        GetOrderDetails(db),
        GetUserProfile(db),
        GetOrderHistory(db),
        GetReturnStatus(db),
        ChangeDeliveryDate(db),
        ChangeDeliveryAddress(db),
    ]