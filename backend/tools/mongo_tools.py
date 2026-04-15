# backend/tools/mongo_tools.py

import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
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

def serialize_dates(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, list):
        return [serialize_dates(i) for i in obj]
    if isinstance(obj, dict):
        return {k: serialize_dates(v) for k, v in obj.items()}
    return obj


# ── 0. Think Tool ───────────────────────────────────────────────────────────────
# A no-op tool that forces the model to externalise its reasoning before
# calling any data-fetching tool. Costs almost nothing (result is just
# {"ok": true}) but prevents the most common agent mistakes on smaller models:
#   - Calling get_order_details without a confirmed ID
#   - Calling change_delivery_date without reading warehouse date first
#   - Making a tool call when the answer is already in history

class ThinkTool(BaseTool):
    @property
    def name(self) -> str:
        return "think"

    @property
    def description(self) -> str:
        return (
            "A private reasoning scratchpad. Call this ALONE and FIRST before any "
            "data tool or action tool. Use it to answer: "
            "(1) What exactly is the customer asking? "
            "(2) What data do I already have in conversation history — do I need to fetch anything? "
            "(3) What is the single correct next step? "
            "Output is never shown to the customer. "
            "Do NOT call think at the same time as any other tool. "
            "Do NOT call think a second time in the same reasoning chain unless you received new data."
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
        # No-op — the value is in forcing the model to write the reasoning,
        # not in doing anything with it.
        reasoning = kwargs.get("reasoning", "")
        logger.debug(f"[THINK] {reasoning[:200]}")
        return {"ok": True}


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
            "Fetch full details for one specific order by order_id: product list, status, "
            "shipping address, estimated_warehouse_date, estimated_destination_date, "
            "date-change requests, and return requests on the order. "
            "Use when you need order data to answer a question, OR when computing "
            "the earliest possible delivery date for a date-change request "
            "(read estimated_warehouse_date, add 1 day). "
            "PREREQUISITE: you must have a confirmed order_id from the customer or from get_order_history. "
            "IMPORTANT: Do NOT re-fetch if order details are already visible in conversation history — "
            "reuse the data that's already there."
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

            invoice = None
            if order.get("invoiceId"):
                try:
                    inv_id = ObjectId(str(order["invoiceId"]))
                    invoice = await self._db.invoices.find_one({"_id": inv_id})
                except Exception:
                    pass

            data = _serialize(order)

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
            "Fetch a customer's account profile: name, email, loyalty tier, "
            "loyalty points balance, and account status (active / suspended / banned). "
            "Use when the customer asks about their points balance, tier, account standing, "
            "or personal details. "
            "PREREQUISITE: you must have the customer's email address. "
            "Do NOT call this to look up order data — use get_order_history or get_order_details instead."
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
            "List all recent orders for a customer by email: order_id, status, "
            "item names, and estimated delivery dates. "
            "Call first whenever a customer asks about 'my order' without specifying which one, "
            "or when you need an order_id and don't have one yet. "
            "If multiple orders exist, show the list and ask the customer to pick one "
            "BEFORE calling get_order_details or any action tool. "
            "If only one order exists, proceed to get_order_details directly. "
            "PREREQUISITE: you must have the customer's email address. "
            "Do NOT call this if order history was already fetched this session — use what's in history."
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
            "Check the status of an existing return request for a specific order by order_id. "
            "Returns: return status (pending / approved / rejected), submission date, items, "
            "refund method, and any admin resolution notes. "
            "Use when the customer asks 'what happened to my return', 'has my return been approved', "
            "or 'where is my refund'. "
            "PREREQUISITE: you must have a confirmed order_id. "
            "If order_id is not known, call get_order_history first."
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
            "Submit a request to change the estimated delivery date of an order. "
            "Creates a PENDING APPROVAL request — the date is NOT instantly changed. "
            "\n\n"
            "PREREQUISITES — do NOT call this tool until ALL of the following are true:\n"
            "  1. You have a confirmed order_id.\n"
            "  2. You have a specific date the customer has explicitly confirmed they want.\n"
            "\n"
            "CRITICAL — IF THE CUSTOMER SAID 'sooner', 'earlier', 'as soon as possible', "
            "or gave NO specific date:\n"
            "  → Do NOT call this tool yet and do NOT ask the customer for a date "
            "(they cannot know what dates are possible).\n"
            "  → call get_order_details first and read 'estimated_warehouse_date'.\n"
            "  → Compute: earliest_possible = warehouse_date + 1 calendar day.\n"
            "  → Tell the customer: 'The earliest I can request is [date]. "
            "Shall I submit that for you?'\n"
            "  → Wait for a yes/no. Only call this tool after they confirm.\n"
            "\n"
            "OUTCOMES this tool returns:\n"
            "  pending_approval → request submitted, admin reviews within 24 hours\n"
            "  rejected         → not eligible; reason and earliest_possible in result\n"
            "  already_pending  → an existing request is under review, cannot create another"
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

            if status in ("Delivered", "Completed", "Cancelled"):
                return self.success({
                    "outcome": "rejected",
                    "reason": (
                        f"Your order is already '{status}' — "
                        "the delivery date cannot be changed at this stage."
                    ),
                    "order_id": order_id,
                })

            warehouse_dt = order.get("estimated_warehouse_date")
            if not warehouse_dt:
                return self.error(
                    "Order is missing warehouse date — cannot evaluate request."
                )

            if isinstance(warehouse_dt, datetime) and warehouse_dt.tzinfo is None:
                warehouse_dt = warehouse_dt.replace(timezone.utc)

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
            "Only possible while the order is in 'In process' or 'Ready for delivery' status. "
            "Once shipped, the address cannot be changed. "
            "\n\n"
            "PREREQUISITES — do NOT call this tool until ALL of the following are confirmed:\n"
            "  1. You have a confirmed order_id.\n"
            "  2. You have the COMPLETE new address from the customer — all of: "
            "street_and_number, city, country. Postcode (cp) is strongly recommended. "
            "If any field is missing, ask for all missing fields in a single message first.\n"
            "  3. The customer has confirmed the address is correct.\n"
            "\n"
            "OUTCOMES this tool returns:\n"
            "  updated  → address successfully changed\n"
            "  rejected → order status does not allow address changes (already shipped)"
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

# ── 7. Get Order Tracking ──────────────────────────────────────────────────

class GetOrderTracking(BaseTool):
    def __init__(self, db: AsyncIOMotorDatabase):
        self._db = db

    @property
    def name(self) -> str:
        return "get_order_tracking"

    @property
    def description(self) -> str:
        return (
            "Get complete live tracking info for one order: current status, all estimated dates "
            "(warehouse, shipped, destination), shipping address, product list, "
            "any pending date-change requests, and full status history. "
            "Use when the customer asks 'where is my order', 'track my package', "
            "'when will it arrive', or 'what is the status of my order'. "
            "PREREQUISITES: you must have (1) a confirmed order_id AND (2) the customer's email. "
            "If order_id is not known, call get_order_history first."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The confirmed order ID (ObjectId string) from get_order_history"
                },
                "email": {
                    "type": "string",
                    "description": "The customer's email address"
                }
            },
            "required": ["order_id", "email"]
        }

    async def execute(self, **kwargs: Any) -> dict:
        order_id = kwargs.get("order_id", "").strip()
        email    = kwargs.get("email", "").strip().lower()

        if not order_id:
            return self.error("order_id is required.")
        if not email:
            return self.error("email is required.")

        try:
            oid = ObjectId(order_id)
        except Exception:
            return self.error("Invalid order ID format.")

        user = await self._db.users.find_one(
            {"email": {"$regex": f"^{email}$", "$options": "i"}},
            {"_id": 1}
        )
        if not user:
            return self.error(f"No account found for email: {email}")

        order = await self._db.orders.find_one({"_id": oid})
        if not order:
            return self.error(f"No order found with ID {order_id}.")

        if str(user["_id"]) != str(order.get("userId")):
            return self.error("This order does not belong to the provided email.")

        invoice = None
        if order.get("invoiceId"):
            try:
                invoice = await self._db.invoices.find_one({"_id": ObjectId(order["invoiceId"])})
            except Exception:
                pass

        return_doc = await self._db.returns.find_one({"orderId": oid})

        tracking = {
            "order_id": str(order["_id"]),
            "status": order.get("status", "Unknown"),
            "created_at": order.get("createdAt").isoformat() if isinstance(order.get("createdAt"), datetime) else str(order.get("createdAt")),
            "estimated_warehouse_date": order.get("estimated_warehouse_date").isoformat() if isinstance(order.get("estimated_warehouse_date"), datetime) else None,
            "estimated_shipped_date": order.get("estimated_shipped_date").isoformat() if isinstance(order.get("estimated_shipped_date"), datetime) else None,
            "estimated_destination_date": order.get("estimated_destination_date").isoformat() if isinstance(order.get("estimated_destination_date"), datetime) else None,
            "shipping_address": order.get("shipping_address", {}),
            "products": [
                {
                    "name": p.get("name", "Unknown"),
                    "quantity": p.get("quantity", 1)
                } for p in order.get("products", [])
            ],
            "delivery_date_change_request": order.get("delivery_date_change_request"),
            "invoice": {
                "total_amount": invoice.get("totalAmount") if invoice else order.get("totalAmount"),
                "status": invoice.get("status") if invoice else None,
                "invoice_number": invoice.get("metadata", {}).get("erpDetails", {}).get("invoiceNumber") if invoice else None,
            } if invoice or "totalAmount" in order else None,
            "return_status": _serialize(return_doc) if return_doc else None,
            "status_history": serialize_dates(order.get("status_history", []))
        }

        return self.success(serialize_dates({
            "tracking": tracking,
            "message": "Here is the complete tracking information for your order."
        }))

# ── 8. Get Invoice Details ──────────────────────────────────────────────────

class GetInvoiceDetails(BaseTool):
    def __init__(self, db: AsyncIOMotorDatabase):
        self._db = db

    @property
    def name(self) -> str:
        return "get_invoice_details"

    @property
    def description(self) -> str:
        return (
            "Fetch invoice and payment details for one specific order: total paid, subtotal, "
            "tax, invoice number, due date, transaction ID, approval code, and loyalty points earned. "
            "Use when the customer asks about their invoice, receipt, payment amount, tax, "
            "or transaction details. "
            "PREREQUISITES: you must have (1) a confirmed order_id AND (2) the customer's email. "
            "If order_id is not known, call get_order_history first."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The specific order ID for which invoice details are needed"
                },
                "email": {
                    "type": "string",
                    "description": "The customer's email address"
                }
            },
            "required": ["order_id", "email"]
        }

    async def execute(self, **kwargs: Any) -> dict:
        order_id = kwargs.get("order_id", "").strip()
        email    = kwargs.get("email", "").strip().lower()

        if not order_id:
            return self.error("order_id is required.")
        if not email:
            return self.error("email is required.")

        try:
            user = await self._db.users.find_one(
                {"email": {"$regex": f"^{email}$", "$options": "i"}},
                {"_id": 1}
            )
            if not user:
                return self.error(f"No account found for email: {email}")

            order = await self._db.orders.find_one({"_id": ObjectId(order_id)})
            if not order:
                return self.error(f"No order found with ID {order_id}")

            if str(user["_id"]) != str(order.get("userId")):
                return self.error("This order does not belong to the provided email.")

            invoice = None
            if order.get("invoiceId"):
                invoice = await self._db.invoices.find_one({"_id": ObjectId(order["invoiceId"])})

            if not invoice:
                return self.error("No invoice found for this order.")

            erp     = invoice.get("metadata", {}).get("erpDetails", {})
            card    = invoice.get("metadata", {}).get("creditCardProcessing", {})
            loyalty = invoice.get("metadata", {}).get("loyaltyRewards", {})

            data = {
                "invoice_id":             str(invoice.get("_id")),
                "order_id":               order_id,
                "invoice_number":         erp.get("invoiceNumber"),
                "status":                 invoice.get("status"),
                "total_amount":           invoice.get("totalAmount"),
                "subtotal":               erp.get("subtotal"),
                "total_tax":              erp.get("totalTax"),
                "due_date":               erp.get("dueDate"),
                "payment_terms":          erp.get("paymentTerms"),
                "transaction_id":         card.get("transactionId"),
                "approval_code":          card.get("approvalCode"),
                "loyalty_points_earned":  loyalty.get("pointsEarned"),
                "loyalty_tier":           loyalty.get("tier"),
            }

            return self.success({
                "invoice": data,
                "message": "Here are the invoice and payment details for your order."
            })

        except Exception as e:
            logger.exception("get_invoice_details failed")
            return self.error(f"Failed to fetch invoice details: {str(e)}")

# ── 9. Get Total Amount Paid ──────────────────────────────────────────────────

class GetTotalAmountPaid(BaseTool):
    def __init__(self, db: AsyncIOMotorDatabase):
        self._db = db

    @property
    def name(self) -> str:
        return "get_total_amount_paid"

    @property
    def description(self) -> str:
        return (
            "Get total lifetime spending summary across ALL orders for a customer: "
            "grand total paid, total order count, average order value, "
            "highest and lowest single order, and date range of purchases. "
            "Use ONLY when the customer asks how much they have spent in total, "
            "or asks for a spending summary across all their orders. "
            "Do NOT use for single-order questions — use get_invoice_details instead. "
            "PREREQUISITE: you must have the customer's email address."
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

            user_id = user["_id"]

            pipeline = [
                {"$match": {"userId": user_id}},
                {
                    "$addFields": {
                        "createdAt": {
                            "$cond": {
                                "if": {"$eq": [{"$type": "$createdAt"}, "string"]},
                                "then": {"$toDate": "$createdAt"},
                                "else": "$createdAt"
                            }
                        }
                    }
                },
                {
                    "$lookup": {
                        "from": "invoices",
                        "localField": "_id",
                        "foreignField": "orderId",
                        "as": "invoice"
                    }
                },
                {
                    "$unwind": {
                        "path": "$invoice",
                        "preserveNullAndEmptyArrays": True
                    }
                },
                {
                    "$addFields": {
                        "totalAmount": {
                            "$ifNull": ["$invoice.totalAmount", 0]
                        }
                    }
                },
                {
                    "$addFields": {
                        "totalAmount": {
                            "$cond": {
                                "if": {"$isNumber": "$totalAmount"},
                                "then": "$totalAmount",
                                "else": 0
                            }
                        }
                    }
                },
                {
                    "$group": {
                        "_id": None,
                        "total_amount_paid": {"$sum": "$totalAmount"},
                        "total_orders":      {"$sum": 1},
                        "highest_order":     {"$max": "$totalAmount"},
                        "lowest_order":      {"$min": "$totalAmount"},
                        "first_purchase":    {"$min": "$createdAt"},
                        "last_purchase":     {"$max": "$createdAt"}
                    }
                }
            ]

            result = await self._db.orders.aggregate(pipeline).to_list(length=1)

            if not result or not result[0].get("total_orders"):
                return self.success({
                    "total_amount_paid": 0,
                    "total_orders": 0,
                    "message": "You haven't made any purchases yet."
                })

            stats   = result[0]
            average = round(stats["total_amount_paid"] / stats["total_orders"], 2) if stats["total_orders"] > 0 else 0

            data = {
                "total_amount_paid":     stats["total_amount_paid"],
                "total_orders":          stats["total_orders"],
                "average_order_value":   average,
                "highest_order_amount":  stats.get("highest_order", 0),
                "lowest_order_amount":   stats.get("lowest_order", 0),
                "first_purchase_date":   stats.get("first_purchase").isoformat() if isinstance(stats.get("first_purchase"), datetime) else None,
                "last_purchase_date":    stats.get("last_purchase").isoformat() if isinstance(stats.get("last_purchase"), datetime) else None,
            }

            return self.success({
                "spending_summary": data,
                "message": "Here is your complete spending summary across all purchases."
            })

        except Exception as e:
            logger.exception("get_total_amount_paid failed")
            return self.error(f"Failed to calculate total spending: {str(e)}")

# ── 10. Initiate Return ──────────────────────────────────────────────────

class InitiateReturn(BaseTool):
    def __init__(self, db: AsyncIOMotorDatabase):
        self._db = db
 
    @property
    def name(self) -> str:
        return "initiate_return"
 
    @property
    def description(self) -> str:
        return (
            "Submit a return request for a delivered order. "
            "Checks the return window (30 days standard, 45 days for Platinum members). "
            "Creates a PENDING APPROVAL request — the return is NOT instantly approved. "
            "\n\n"
            "PREREQUISITES — do NOT call this tool until ALL of the following are confirmed:\n"
            "  1. You have a confirmed order_id.\n"
            "  2. The order status is 'Delivered' (verify from order details in history).\n"
            "  3. You know which item(s) the customer wants to return — use exact product names "
            "from the order.\n"
            "  4. You have a return reason — must be one of: defective_damaged, "
            "wrong_item_received, not_as_described, changed_mind, size_fit_issue.\n"
            "  5. You have the customer's preferred refund method: "
            "original_payment, store_credit, or bank_transfer.\n"
            "\n"
            "If ANY of items 3, 4, or 5 are missing, ask for ALL missing ones in a "
            "SINGLE message before calling this tool. Never call with guessed values.\n"
            "\n"
            "OUTCOMES this tool returns:\n"
            "  pending_approval → submitted, admin reviews within 24 hours\n"
            "  rejected         → order not delivered or outside return window (see reason)\n"
            "  already_pending  → a return request is already under review"
        )
 
    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The confirmed order ID from get_order_history"
                },
                "email": {
                    "type": "string",
                    "description": "The customer's email address"
                },
                "reason": {
                    "type": "string",
                    "enum": [
                        "defective_damaged",
                        "wrong_item_received",
                        "not_as_described",
                        "changed_mind",
                        "size_fit_issue"
                    ],
                    "description": "The reason for the return"
                },
                "refund_method": {
                    "type": "string",
                    "enum": ["original_payment", "store_credit", "bank_transfer"],
                    "description": "The customer's preferred refund method"
                },
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of item names to return. "
                        "Use the product names from the order. "
                        "If all items are being returned, include all of them."
                    )
                }
            },
            "required": ["order_id", "email", "reason", "refund_method", "items"]
        }
 
    async def execute(self, **kwargs: Any) -> dict:
        order_id      = kwargs.get("order_id", "").strip()
        email         = kwargs.get("email", "").strip().lower()
        reason        = kwargs.get("reason", "").strip()
        refund_method = kwargs.get("refund_method", "").strip()
        items         = kwargs.get("items", [])
 
        if not order_id:
            return self.error("order_id is required.")
        if not email:
            return self.error("email is required.")
        if not reason:
            return self.error("reason is required.")
        if not refund_method:
            return self.error("refund_method is required.")
        if not items:
            return self.error("At least one item must be specified for return.")
 
        try:
            oid = ObjectId(order_id)
        except Exception:
            return self.error(f"'{order_id}' is not a valid order ID.")
 
        # ── Resolve user from email ──────────────────────────────────────────
        user = await self._db.users.find_one(
            {"email": {"$regex": f"^{email}$", "$options": "i"}},
            {"_id": 1, "loyaltyTier": 1}
        )
        if not user:
            return self.error(f"No account found for email: {email}")
 
        # ── Fetch order ──────────────────────────────────────────────────────
        try:
            order = await self._db.orders.find_one(
                {"_id": oid},
                {
                    "status": 1,
                    "userId": 1,
                    "products": 1,
                    "estimated_destination_date": 1,
                    "return_request": 1,
                }
            )
        except Exception as e:
            logger.exception(f"initiate_return order fetch failed for {order_id}")
            return self.error(f"Failed to fetch order: {str(e)}")
 
        if not order:
            return self.error(f"No order found with ID {order_id}.")
 
        # ── Verify ownership ─────────────────────────────────────────────────
        if str(user["_id"]) != str(order.get("userId")):
            return self.error("This order does not belong to the provided email.")
 
        # ── Check order status ───────────────────────────────────────────────
        status = order.get("status", "")
        if status != "Delivered":
            return self.success({
                "outcome": "rejected",
                "reason": (
                    f"Your order is currently '{status}'. "
                    "Returns can only be initiated after the order has been delivered."
                ),
                "current_status": status,
            })
 
        # ── Check return window ──────────────────────────────────────────────
        loyalty_tier   = user.get("loyaltyTier", "Bronze")
        return_window  = 45 if loyalty_tier == "Platinum" else 30
 
        delivery_date = order.get("estimated_destination_date")
        if not delivery_date:
            return self.error(
                "Order is missing delivery date — cannot evaluate return window."
            )
 
        if isinstance(delivery_date, datetime) and delivery_date.tzinfo is None:
            delivery_date = delivery_date.replace(timezone.utc)
 
        now = datetime.now(timezone.utc)
        days_since_delivery = (now - delivery_date).days
 
        if days_since_delivery > return_window:
            return self.success({
                "outcome": "rejected",
                "reason": (
                    f"Your return window has expired. "
                    f"The order was delivered on {delivery_date.strftime('%B %d, %Y')} "
                    f"({days_since_delivery} days ago). "
                    f"{'Platinum members have' if loyalty_tier == 'Platinum' else 'Your'} "
                    f"return window is {return_window} days."
                ),
                "delivered_on":   delivery_date.date().isoformat(),
                "return_window":  return_window,
                "days_elapsed":   days_since_delivery,
            })
 
        # ── Check for existing pending return request ────────────────────────
        existing = order.get("return_request")
        if existing and existing.get("status") == "pending":
            return self.success({
                "outcome": "already_pending",
                "reason": (
                    "There is already a pending return request for this order. "
                    "Our team is reviewing it and will confirm within 24 hours. "
                    "Please wait for that confirmation before submitting a new request."
                ),
                "request_id": existing.get("request_id"),
            })
 
        # ── Determine who covers return shipping ─────────────────────────────
        leafy_covers = {"defective_damaged", "wrong_item_received", "not_as_described"}
        return_shipping_covered_by = (
            "leafy" if reason in leafy_covers else "customer"
        )
 
        # ── Insert into pending_requests ─────────────────────────────────────
        now = datetime.now(timezone.utc)
 
        pending_doc = {
            "type":                        "return_request",
            "status":                      "pending",
            "order_id":                    oid,
            "user_id":                     order.get("userId"),
            "reason":                      reason,
            "items":                       items,
            "refund_method":               refund_method,
            "return_shipping_covered_by":  return_shipping_covered_by,
            "loyalty_tier":                loyalty_tier,
            "delivery_date":               delivery_date,
            "days_since_delivery":         days_since_delivery,
            "created_at":                  now,
            "resolved_at":                 None,
            "resolved_by":                 None,
            "resolution_note":             None,
            "session_id":                  None,
        }
 
        result = await self._db.pending_requests.insert_one(pending_doc)
        request_id = str(result.inserted_id)
 
        # ── Mirror lightweight reference back to order ────────────────────────
        await self._db.orders.update_one(
            {"_id": oid},
            {"$set": {
                "return_request": {
                    "request_id": request_id,
                    "status":     "pending",
                    "reason":     reason,
                    "items":      items,
                    "created_at": now,
                }
            }}
        )
 
        # ── Broadcast to all connected CRM admin tabs ─────────────────────────
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
            "outcome":    "pending_approval",
            "request_id": request_id,
            "message": (
                "Your return request has been submitted and is pending approval. "
                "Our team will review it within 24 hours and send you an RMA number "
                "to ship the item(s) back. "
                f"Return shipping will be covered by "
                f"{'Leafy' if return_shipping_covered_by == 'leafy' else 'you (the customer)'}."
            ),
            "items":                      items,
            "reason":                     reason,
            "refund_method":              refund_method,
            "return_shipping_covered_by": return_shipping_covered_by,
        })

# ── 11. Change Order Item Characteristics (Size, Color, etc.) ─────────────────

class ChangeOrderItem(BaseTool):
    def __init__(self, db: AsyncIOMotorDatabase):
        self._db = db

    @property
    def name(self) -> str:
        return "change_order_item"

    @property
    def description(self) -> str:
        return (
            "Submit a request to change the size and/or colour of an item in an order. "
            "For 'Processing'/'In process' orders: checks product catalogue stock. "
            "For other active statuses: checks warehouse stock. "
            "In both cases the request goes to admin for approval — nothing is changed instantly. "
            "\n\n"
            "PREREQUISITES — do NOT call this tool until ALL of the following are confirmed:\n"
            "  1. You have a confirmed order_id.\n"
            "  2. You have the exact item name as it appears in the order.\n"
            "  3. You have the desired size (if changing) AND/OR desired colour (if changing). "
            "If the customer mentioned only one (e.g. only size, not colour), ask for "
            "the other in the same message before proceeding.\n"
            "  4. The customer has explicitly confirmed the full change "
            "(e.g. 'Yes, change to size M in red').\n"
            "\n"
            "Do NOT call with partial info. Collect everything in one message if needed.\n"
            "\n"
            "OUTCOMES this tool returns:\n"
            "  pending_approval → stock confirmed, request sent for admin review\n"
            "  out_of_stock     → requested variant not available in stock\n"
            "  rejected         → order status or variant does not allow the change\n"
            "  already_pending  → an item change request is already under review"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "Confirmed order ID from get_order_history"
                },
                "item_name": {
                    "type": "string",
                    "description": "Exact product name from the order"
                },
                "new_size": {
                    "type": "string",
                    "description": "New size (e.g. M, L). Omit if not changing size."
                },
                "new_color": {
                    "type": "string",
                    "description": "New colour. Omit if not changing colour."
                },
                "email": {
                    "type": "string",
                    "description": "Customer email address"
                }
            },
            "required": ["order_id", "item_name", "email"]
        }

    async def execute(self, **kwargs: Any) -> dict:
        order_id  = kwargs.get("order_id", "").strip()
        item_name = kwargs.get("item_name", "").strip()
        new_size  = kwargs.get("new_size", "").strip()
        new_color = kwargs.get("new_color", "").strip()
        email     = kwargs.get("email", "").strip().lower()

        if not order_id or not item_name or not email:
            return self.error("order_id, item_name, and email are required.")

        if not new_size and not new_color:
            return self.error("At least one of new_size or new_color must be provided.")

        try:
            oid = ObjectId(order_id)
        except Exception:
            return self.error("Invalid order ID format.")

        # ── Resolve user ─────────────────────────────────────────────────────
        user = await self._db.users.find_one(
            {"email": {"$regex": f"^{email}$", "$options": "i"}},
            {"_id": 1}
        )
        if not user:
            return self.error(f"No account found for email: {email}")

        # ── Fetch order ──────────────────────────────────────────────────────
        order = await self._db.orders.find_one({"_id": oid})
        if not order:
            return self.error(f"No order found with ID {order_id}.")

        if str(order.get("userId")) != str(user["_id"]):
            return self.error("This order does not belong to the provided email.")

        status   = order.get("status", "")
        products = order.get("products", [])

        # ── Terminal states — no action possible ─────────────────────────────
        if status in ("Delivered", "Completed", "Cancelled"):
            return self.success({
                "outcome": "rejected",
                "reason": (
                    f"Your order is '{status}'. "
                    "Item characteristics can no longer be changed at this stage."
                ),
            })

        # ── Block if a change request is already pending ──────────────────────
        existing = order.get("item_change_request")
        if existing and existing.get("status") == "pending":
            return self.success({
                "outcome": "already_pending",
                "reason": (
                    "There is already a pending item change request for this order. "
                    "Our team is reviewing it and will confirm within 24 hours. "
                    "Please wait for that confirmation before submitting a new request."
                ),
                "request_id": existing.get("request_id"),
            })

        # ── Find the item in the order ────────────────────────────────────────
        target_item = None
        for item in products:
            if item.get("name", "").lower() == item_name.lower():
                target_item = item
                break

        if not target_item:
            return self.error(f"Item '{item_name}' not found in this order.")

        current_size  = target_item.get("variant", "").get("size", "")
        current_color = target_item.get("variant", "").get("color", "")
        check_size    = new_size  if new_size  else current_size
        check_color   = new_color if new_color else current_color

        now              = datetime.now(timezone.utc)
        stock_source     = None
        warehouse_doc    = None

        # ════════════════════════════════════════════════════════════════════
        # SCENARIO 1 — Processing / In process: check products table
        # ════════════════════════════════════════════════════════════════════
        if status in ("Processing", "In process"):

            product_doc = await self._db.products.find_one(
                {"name": {"$regex": f"^{re.escape(item_name)}$", "$options": "i"}},
                {"variants": 1, "name": 1}
            )

            if not product_doc:
                return self.error(
                    f"Product '{item_name}' not found in the product catalogue."
                )

            matched_variant = None
            for variant in product_doc.get("variants", []):
                size_match  = (variant.get("size", "").lower()  == check_size.lower())  if check_size  else True
                color_match = (variant.get("color", "").lower() == check_color.lower()) if check_color else True
                if size_match and color_match:
                    matched_variant = variant
                    break

            if not matched_variant:
                return self.success({
                    "outcome": "rejected",
                    "reason": (
                        f"The variant "
                        f"{'size ' + check_size + ' ' if check_size else ''}"
                        f"{'colour ' + check_color if check_color else ''}"
                        f"does not exist for '{item_name}'."
                    ),
                })

            if matched_variant.get("stock", 0) <= 0:
                return self.success({
                    "outcome": "out_of_stock",
                    "reason": (
                        f"The requested variant of '{item_name}' "
                        f"({'size ' + check_size + ', ' if check_size else ''}"
                        f"{'colour ' + check_color if check_color else ''}) "
                        "is currently out of stock."
                    ),
                })

            stock_source = "products"

        # ════════════════════════════════════════════════════════════════════
        # SCENARIO 2 — Any other active status: check warehouse stock
        # ════════════════════════════════════════════════════════════════════
        else:
            warehouse_doc = await self._db.warehouses.find_one({
                "inventory": {
                    "$elemMatch": {
                        "name":  {"$regex": f"^{re.escape(item_name)}$", "$options": "i"},
                        "size":  check_size  if check_size  else {"$exists": True},
                        "color": check_color if check_color else {"$exists": True},
                        "stock": {"$gt": 0},
                    }
                }
            })

            if not warehouse_doc:
                return self.success({
                    "outcome": "rejected",
                    "reason": (
                        f"Your order is currently '{status}'. "
                        f"The requested variant of '{item_name}' "
                        f"({'size ' + check_size + ', ' if check_size else ''}"
                        f"{'colour ' + check_color if check_color else ''}) "
                        "is not available in any warehouse. "
                        "We are unable to process this change at this time."
                    ),
                })

            stock_source = "warehouse"

        # ── Build change description for status_history note ─────────────────
        change_parts = []
        if new_size:
            change_parts.append(f"size {current_size} → {check_size}")
        if new_color:
            change_parts.append(f"colour {current_color} → {check_color}")
        change_description = ", ".join(change_parts)

        warehouse_note = (
            f" Stock confirmed in {warehouse_doc.get('city')} warehouse."
            if warehouse_doc else ""
        )

        # ── Insert into pending_requests ──────────────────────────────────────
        pending_doc = {
            "type":            "item_change",
            "status":          "pending",
            "order_id":        oid,
            "user_id":         order.get("userId"),
            "item_name":       item_name,
            "new_size":        check_size,
            "new_color":       check_color,
            "old_size":        current_size,
            "old_color":       current_color,
            "stock_source":    stock_source,
            "warehouse_id":    str(warehouse_doc["_id"]) if warehouse_doc else None,
            "warehouse_city":  warehouse_doc.get("city") if warehouse_doc else None,
            "order_status":    status,
            "created_at":      now,
            "resolved_at":     None,
            "resolved_by":     None,
            "resolution_note": None,
            "session_id":      None,
        }

        result     = await self._db.pending_requests.insert_one(pending_doc)
        request_id = str(result.inserted_id)

        # ── Mirror to order + push to status_history ─────────────────────────
        await self._db.orders.update_one(
            {"_id": oid},
            {
                "$set": {
                    "item_change_request": {
                        "request_id":     request_id,
                        "status":         "pending",
                        "item_name":      item_name,
                        "new_size":       check_size,
                        "new_color":      check_color,
                        "old_size":       current_size,
                        "old_color":      current_color,
                        "warehouse_city": warehouse_doc.get("city") if warehouse_doc else None,
                        "created_at":     now,
                    }
                },
                "$push": {
                    "status_history": {
                        "status":     "Item Change Pending Approval",
                        "note": (
                            f"Change request submitted for '{item_name}': "
                            f"{change_description}.{warehouse_note} "
                            f"Awaiting admin approval."
                        ),
                        "timestamp":  now,
                        "updated_by": "system",
                    }
                }
            }
        )

        # ── Broadcast to CRM admins ───────────────────────────────────────────
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
            "outcome":    "pending_approval",
            "request_id": request_id,
            "message": (
                "Your item change request has been submitted and is pending admin approval. "
                "You will be notified within 24 hours."
            ),
            "new_size":  check_size,
            "new_color": check_color,
        })

# ── 12. Cancel Order Tool ─────────────────────────────────────────────────────
class CancelOrder(BaseTool):
    def __init__(self, db: AsyncIOMotorDatabase):
        self._db = db

    @property
    def name(self) -> str:
        return "cancel_order"

    @property
    def description(self) -> str:
        return (
            "Cancel a customer's order. Cancellation is ONLY possible when the order "
            "status is 'Processing' or 'In process'. "
            "If the order is Shipped, In Transit, Out for Delivery, Delivered, or "
            "already Cancelled, cancellation is not possible — advise the customer to "
            "wait for delivery and then initiate a return instead. "
            "On successful cancellation, a full refund including original shipping cost "
            "is issued to the original payment method within 3–5 business days. "
            "\n\n"
            "PREREQUISITES — do NOT call this tool until ALL of the following are confirmed:\n"
            "  1. You have a confirmed order_id. If not known, call get_order_history first.\n"
            "  2. The customer has explicitly confirmed they want to cancel this order "
            "(do not cancel without confirmation — it is irreversible).\n"
            "  3. You have verified the order is in 'Processing' or 'In process' status "
            "from order details in history.\n"
            "\n"
            "Reason is optional — you may ask but do not block on it.\n"
            "\n"
            "OUTCOMES this tool returns:\n"
            "  success           → order cancelled, refund initiated\n"
            "  rejected          → order not in a cancellable status\n"
            "  already_cancelled → order was already cancelled"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The confirmed order ID to cancel"
                },
                "email": {
                    "type": "string",
                    "description": "Customer email for verification"
                },
                "reason": {
                    "type": "string",
                    "description": "Optional reason provided by customer (e.g. 'changed mind', 'found better price')"
                }
            },
            "required": ["order_id", "email"]
        }

    async def execute(self, **kwargs: Any) -> dict:
        order_id = kwargs.get("order_id", "").strip()
        email    = kwargs.get("email", "").strip().lower()
        reason   = kwargs.get("reason", "").strip()

        if not order_id or not email:
            return self.error("order_id and email are required.")

        try:
            oid = ObjectId(order_id)
        except Exception:
            return self.error(f"'{order_id}' is not a valid order ID.")

        # Get user
        user = await self._db.users.find_one(
            {"email": {"$regex": f"^{email}$", "$options": "i"}},
            {"_id": 1, "name": 1, "surname": 1}
        )
        if not user:
            return self.error(f"No account found for email: {email}")

        # Get order
        order = await self._db.orders.find_one({"_id": oid})
        if not order:
            return self.error(f"No order found with ID {order_id}.")

        if str(order.get("userId")) != str(user["_id"]):
            return self.error("This order does not belong to the provided customer.")

        status = order.get("status", "")

        # Policy Check: Only Processing orders can be cancelled
        if status not in ["Processing", "In process"]:
            return self.success({
                "outcome": "rejected",
                "reason": (
                    f"Your order is currently '{status}'. "
                    "According to our policy, orders can only be cancelled while they are still in 'Processing' status. "
                    "If the order has already shipped, please wait for delivery and initiate a return instead."
                ),
                "current_status": status
            })

        # Check if already cancelled
        if status == "Cancelled":
            return self.success({
                "outcome": "already_cancelled",
                "reason": "This order has already been cancelled."
            })

        now = datetime.now(timezone.utc)

        # Perform cancellation
        await self._db.orders.update_one(
            {"_id": oid},
            {
                "$set": {
                    "status": "Cancelled",
                    "cancelled_at": now,
                    "cancellation_reason": reason or "Customer requested cancellation",
                    "refund_status": "Pending"
                },
                "$push": {
                    "status_history": {
                        "status": "Cancelled",
                        "timestamp": now,
                        "note": f"Cancelled by customer request. Reason: {reason or 'Not specified'}",
                        "updated_by": "system"
                    }
                }
            }
        )

        # Optional: Create a simple cancellation record (for audit)
        await self._db.cancellations.insert_one({
            "order_id": oid,
            "user_id": user["_id"],
            "reason": reason or "Customer requested",
            "cancelled_at": now,
            "refund_amount": order.get("total_amount", 0),   # you may need to calculate this properly
            "refund_method": "original_payment"
        })

        return self.success({
            "outcome": "success",
            "message": (
                "Your order has been successfully cancelled. "
                "A full refund including original shipping cost will be issued to your original payment method "
                "within 3–5 business days."
            ),
            "order_id": order_id,
            "refund_timeline": "3–5 business days"
        })

# ── Registry ────────────────────────────────────────────────────────────────────

def get_all_tools(db: AsyncIOMotorDatabase) -> list[BaseTool]:
    return [
        ThinkTool(),                  # ← no-op reasoning tool, no DB needed
        GetOrderDetails(db),
        GetUserProfile(db),
        GetOrderHistory(db),
        GetReturnStatus(db),
        ChangeDeliveryDate(db),
        ChangeDeliveryAddress(db),
        GetOrderTracking(db),
        GetInvoiceDetails(db),
        GetTotalAmountPaid(db),
        InitiateReturn(db),
        ChangeOrderItem(db),
        CancelOrder(db),
    ]