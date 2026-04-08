# backend/api/admin.py

import uuid
import logging
from datetime import datetime, timezone
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.api.dependencies import get_current_admin
from backend.database import get_db
from backend.api.websocket import ws_manager

from backend.services.conversation_store import ConversationStore
from backend.api.dependencies import get_conversations

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


class ResolutionBody(BaseModel):
    note: str | None = None


def _serialize_request(doc: dict) -> dict:
    result = {}
    for k, v in doc.items():
        if isinstance(v, ObjectId):
            result[k] = str(v)
        elif isinstance(v, datetime):
            result[k] = v.isoformat()
        elif isinstance(v, dict):
            result[k] = _serialize_request(v)
        else:
            result[k] = v
    return result


@router.get("/requests")
async def get_pending_requests(
    status:       str = "pending",
    current_user: dict = Depends(get_current_admin),
    db:           AsyncIOMotorDatabase = Depends(get_db),
):
    """Get all pending requests for CRM dashboard."""
    cursor = db.pending_requests.find(
        {"status": status}
    ).sort("created_at", -1).limit(50)

    requests = []
    async for req in cursor:
        serialized = _serialize_request(req)

        # Enrich with order details
        try:
            order = await db.orders.find_one(
                {"_id": ObjectId(str(req["order_id"]))},
                {
                    "status": 1,
                    "products": 1,
                    "shipping_address": 1,
                    "estimated_destination_date": 1,
                }
            )
            if order:
                serialized["order"] = {
                    "status":   order.get("status"),
                    "address":  order.get("shipping_address"),
                    "products": [
                        p.get("name", "Unknown")
                        for p in order.get("products", [])[:3]
                    ],
                    "current_delivery": (
                        order["estimated_destination_date"].isoformat()
                        if isinstance(
                            order.get("estimated_destination_date"), datetime
                        ) else None
                    ),
                }
        except Exception:
            pass

        # Enrich with customer details
        try:
            user = await db.users.find_one(
                {"_id": ObjectId(str(req["user_id"]))},
                {"name": 1, "surname": 1, "email": 1, "loyaltyTier": 1}
            )
            if user:
                serialized["customer"] = {
                    "name":        f"{user.get('name')} {user.get('surname')}",
                    "email":       user.get("email"),
                    "loyaltyTier": user.get("loyaltyTier"),
                }
        except Exception:
            pass

        requests.append(serialized)

    return {"requests": requests, "total": len(requests)}


@router.post("/requests/{request_id}/approve")
async def approve_request(
    request_id:    str,
    body:          ResolutionBody = ResolutionBody(),
    current_user:  dict = Depends(get_current_admin),
    db:            AsyncIOMotorDatabase = Depends(get_db),
    conversations: ConversationStore = Depends(get_conversations),
):
    """Approve a pending request — handles date_change and return_request types."""
    try:
        rid = ObjectId(request_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request ID.")

    req = await db.pending_requests.find_one({"_id": rid})
    if not req:
        raise HTTPException(status_code=404, detail="Request not found.")
    if req["status"] != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Request is already '{req['status']}'."
        )

    now      = datetime.now(timezone.utc)
    order_id = req["order_id"]

    # ── Mark pending_request as approved (same for all types) ─────────────────
    await db.pending_requests.update_one(
        {"_id": rid},
        {"$set": {
            "status":          "approved",
            "resolved_at":     now,
            "resolved_by":     current_user.get("email"),
            "resolution_note": body.note,
        }}
    )

    # ── Type-specific logic ────────────────────────────────────────────────────

    if req["type"] == "date_change":
        # Apply the new delivery date to the order — behaviour unchanged
        await db.orders.update_one(
            {"_id": order_id},
            {"$set": {
                "estimated_destination_date":               req["requested_value"],
                "delivery_date_change_request.status":      "approved",
                "delivery_date_change_request.resolved_at": now,
            }}
        )

        approval_message = (
            f"Great news! Your delivery date change request has been approved. "
            f"Your new delivery date is "
            f"{req['requested_value'].strftime('%B %d, %Y')}."
        )

    elif req["type"] == "return_request":
        # Generate RMA number and create the returns document
        rma_number = f"RMA-{str(uuid.uuid4()).upper()[:8]}"

        return_doc = {
            "orderId":                    order_id,
            "userId":                     req.get("user_id"),
            "status":                     "Requested",
            "rma_number":                 rma_number,
            "reason":                     req.get("reason"),
            "items":                      req.get("items", []),
            "refund_method":              req.get("refund_method"),
            "return_shipping_covered_by": req.get("return_shipping_covered_by"),
            "created_at":                 req.get("created_at"),
            "approved_at":                now,
            "approved_by":                current_user.get("email"),
            "resolved_at":                None,
            "resolution_note":            body.note,
        }
        await db.returns.insert_one(return_doc)

        # Mirror status + RMA back to order
        await db.orders.update_one(
            {"_id": order_id},
            {"$set": {
                "return_request.status":      "approved",
                "return_request.rma_number":  rma_number,
                "return_request.resolved_at": now,
            }}
        )

        shipping_note = (
            "Leafy will email you a free return label."
            if req.get("return_shipping_covered_by") == "leafy"
            else "Return shipping is at your cost."
        )
        approval_message = (
            f"Great news! Your return request has been approved. "
            f"Your RMA number is {rma_number}. "
            f"Please ship the item(s) back within 7 days. "
            f"{shipping_note}"
        )

    else:
        # Unknown type — still approved in pending_requests above, just log it
        logger.warning(f"approve_request: unknown request type '{req.get('type')}'")
        approval_message = "Your request has been approved."

    logger.info(f"Request {request_id} approved by {current_user.get('email')}")

    # ── Notify customer ────────────────────────────────────────────────────────
    session_id = str(req.get("session_id", ""))

    if session_id:
        await conversations.append_notification(
            session_id = session_id,
            message    = approval_message,
            status     = "approved",
        )

    await ws_manager.notify_session(
        session_id = session_id,
        payload    = {
            "type":    "request_resolved",
            "status":  "approved",
            "message": approval_message,
        }
    )

    return {
        "status":     "approved",
        "request_id": request_id,
        "note":       body.note,
    }


@router.post("/requests/{request_id}/reject")
async def reject_request(
    request_id:    str,
    body:          ResolutionBody = ResolutionBody(),
    current_user:  dict = Depends(get_current_admin),
    db:            AsyncIOMotorDatabase = Depends(get_db),
    conversations: ConversationStore = Depends(get_conversations),
):
    try:
        rid = ObjectId(request_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request ID.")

    req = await db.pending_requests.find_one({"_id": rid})
    if not req:
        raise HTTPException(status_code=404, detail="Request not found.")
    if req["status"] != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Request is already '{req['status']}'."
        )

    now      = datetime.now(timezone.utc)
    order_id = req["order_id"]

    # ── Mark pending_request as rejected (same for all types) ─────────────────
    await db.pending_requests.update_one(
        {"_id": rid},
        {"$set": {
            "status":          "rejected",
            "resolved_at":     now,
            "resolved_by":     current_user.get("email"),
            "resolution_note": body.note,
        }}
    )

    # ── Type-specific logic ────────────────────────────────────────────────────

    if req["type"] == "date_change":
        # Mirror rejection back to order — behaviour unchanged
        await db.orders.update_one(
            {"_id": order_id},
            {"$set": {
                "delivery_date_change_request.status":           "rejected",
                "delivery_date_change_request.resolved_at":      now,
                "delivery_date_change_request.resolution_note":  body.note,
            }}
        )

        rejection_message = (
            "Unfortunately your delivery date change request could not be approved. "
            f"Reason: {body.note or 'No reason provided'}."
        )

    elif req["type"] == "return_request":
        # Mirror rejection back to order
        await db.orders.update_one(
            {"_id": order_id},
            {"$set": {
                "return_request.status":           "rejected",
                "return_request.resolved_at":      now,
                "return_request.resolution_note":  body.note,
            }}
        )

        rejection_message = (
            "Unfortunately your return request could not be approved. "
            f"Reason: {body.note or 'No reason provided'}."
        )

    else:
        logger.warning(f"reject_request: unknown request type '{req.get('type')}'")
        rejection_message = (
            "Unfortunately your request could not be approved. "
            f"Reason: {body.note or 'No reason provided'}."
        )

    logger.info(f"Request {request_id} rejected by {current_user.get('email')}")

    # ── Notify customer ────────────────────────────────────────────────────────
    session_id = str(req.get("session_id", ""))

    if session_id:
        await conversations.append_notification(
            session_id = session_id,
            message    = rejection_message,
            status     = "rejected",
        )

    await ws_manager.notify_session(
        session_id = session_id,
        payload    = {
            "type":    "request_resolved",
            "status":  "rejected",
            "message": rejection_message,
        }
    )

    return {
        "status":     "rejected",
        "request_id": request_id,
        "note":       body.note,
    }


@router.get("/requests/stats")
async def get_stats(
    current_user: dict = Depends(get_current_admin),
    db:           AsyncIOMotorDatabase = Depends(get_db),
):
    """Quick stats for CRM dashboard header."""
    pending  = await db.pending_requests.count_documents({"status": "pending"})
    approved = await db.pending_requests.count_documents({"status": "approved"})
    rejected = await db.pending_requests.count_documents({"status": "rejected"})

    return {
        "pending":  pending,
        "approved": approved,
        "rejected": rejected,
        "total":    pending + approved + rejected,
    }