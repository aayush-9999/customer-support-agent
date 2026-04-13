# backend/api/admin.py

import json
import logging
from datetime import datetime, date, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from backend.api.dependencies import get_current_admin
from backend.core.config import get_settings
from backend.api.websocket import ws_manager
from backend.services.conversation_store import ConversationStore
from backend.api.dependencies import get_conversations
from backend.database_pg import get_pg_session

logger   = logging.getLogger(__name__)
settings = get_settings()
router   = APIRouter(prefix="/admin", tags=["admin"])


class ResolutionBody(BaseModel):
    note: str | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _serialize_request(doc: dict) -> dict:
    """Mongo-only helper: convert ObjectId/datetime fields to strings."""
    from bson import ObjectId
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


def _format_date(value) -> str:
    """
    FIX: Safely format a date or datetime value from a DB row.
    SQLAlchemy may return a plain `datetime.date` (not `datetime`) from a
    DATE column, so calling .strftime on it is fine — but isinstance checks
    against only `datetime` would miss plain `date` objects.
    """
    if isinstance(value, datetime):
        return value.strftime("%B %d, %Y")
    if isinstance(value, date):
        return value.strftime("%B %d, %Y")
    # Fallback for string values (shouldn't happen, but safe)
    return str(value)


# ── PG helpers ────────────────────────────────────────────────────────────────

async def _pg_get_pending_requests(status: str, session: AsyncSession) -> list[dict]:
    rows = await session.execute(
        text("""
            SELECT
                pr.id,
                pr.type,
                pr.status,
                pr.order_id,
                pr.user_id,
                pr.requested_date,
                pr.current_date,
                pr.session_id,
                pr.created_at,
                pr.resolved_at,
                pr.resolved_by,
                pr.resolution_note,
                pr.requested_address,
                pr.requested_city,
                pr.requested_state,
                pr.requested_pincode,
                pr.current_address,
                pr.current_city,
                pr.current_state,
                pr.current_pincode,
                pr.reason,
                pr.items,
                pr.refund_method,
                pr.return_shipping_covered_by,
                pr.reported_items,
                pr.received_items,
                pr.package_condition,
                pr.resolution_type,

                o.order_status,
                o.order_estimated_delivery_date,

                u.name,
                u.surname,
                u.email,
                u.loyalty_tier,

                STRING_AGG(p.product_name, ', ') AS products

            FROM pending_requests pr
            LEFT JOIN orders      o  ON o.order_id   = pr.order_id
            LEFT JOIN users       u  ON u.id          = pr.user_id
            LEFT JOIN order_items oi ON oi.order_id   = pr.order_id
            LEFT JOIN products    p  ON p.product_id  = oi.product_id

            WHERE pr.status = :status

            GROUP BY
                pr.id, pr.type, pr.status, pr.order_id, pr.user_id,
                pr.requested_date, pr.current_date, pr.session_id,
                pr.created_at, pr.resolved_at, pr.resolved_by, pr.resolution_note,
                o.order_status, o.order_estimated_delivery_date,
                u.name, u.surname, u.email, u.loyalty_tier

            ORDER BY pr.created_at DESC
            LIMIT 50
        """),
        {"status": status}
    )
    rows = rows.mappings().all()

    requests = []
    for row in rows:
        requests.append({
            "id":              row["id"],
            "type":            row["type"],
            "status":          row["status"],
            "order_id":        row["order_id"],
            "user_id":         row["user_id"],
            "requested_date":  str(row["requested_date"]) if row["requested_date"] else None,
            "current_date":    str(row["current_date"])   if row["current_date"]   else None,
            "session_id":      row["session_id"],
            "created_at":      str(row["created_at"])     if row["created_at"]     else None,
            "resolved_at":     str(row["resolved_at"])    if row["resolved_at"]    else None,
            "resolved_by":     row["resolved_by"],
            "resolution_note": row["resolution_note"],
            "requested_address": row["requested_address"],
            "requested_city":    row["requested_city"],
            "requested_state":   row["requested_state"],
            "requested_pincode": row["requested_pincode"],
            "current_address":   row["current_address"],
            "current_city":      row["current_city"],
            "current_state":     row["current_state"],
            "current_pincode":   row["current_pincode"],
            "reported_items":   json.loads(row["reported_items"]) if row["reported_items"] else [],
            "received_items":   json.loads(row["received_items"])  if row["received_items"]  else [],
            "package_condition": row["package_condition"],
            "resolution_type":   row["resolution_type"],
            "reason":                      row["reason"],
            "items":                       json.loads(row["items"]) if row["items"] else [],
            "refund_method":               row["refund_method"],
            "return_shipping_covered_by":  row["return_shipping_covered_by"],
            "order": {
                "status":           row["order_status"],
                "current_delivery": str(row["order_estimated_delivery_date"]) if row["order_estimated_delivery_date"] else None,
                "products":         row["products"].split(", ") if row["products"] else [],
            },
            "customer": {
                "name":        f"{row['name']} {row['surname']}",
                "email":       row["email"],
                "loyaltyTier": row["loyalty_tier"],
            },
        })
    return requests

async def _pg_approve_request(
    request_id:    str,
    note:          str | None,
    admin_email:   str,
    session:       AsyncSession,
    conversations: ConversationStore,
) -> dict:
    result = await session.execute(
        text("SELECT * FROM pending_requests WHERE id = :id"),
        {"id": request_id}
    )
    req = result.mappings().first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found.")
    if req["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Request is already '{req['status']}'.")

    now = datetime.now(timezone.utc)

    await session.execute(
        text("""
            UPDATE pending_requests
            SET status          = 'approved',
                resolved_at     = :resolved_at,
                resolved_by     = :resolved_by,
                resolution_note = :note
            WHERE id = :id
        """),
        {"id": request_id, "resolved_at": now, "resolved_by": admin_email, "note": note}
    )

    # ── Branch on request type ────────────────────────────────────────────────

    if req["type"] == "date_change":
        # Strip to plain date — the column is DATE, not TIMESTAMPTZ
        requested_date = req["requested_date"]
        if isinstance(requested_date, datetime):
            requested_date = requested_date.date()
        elif isinstance(requested_date, str):
            requested_date = date.fromisoformat(requested_date.split("T")[0])  # ← add this line

        await session.execute(
            text("""
                UPDATE orders
                SET order_estimated_delivery_date = :requested_date
                WHERE order_id = :order_id
            """),
            {"requested_date": requested_date, "order_id": req["order_id"]}
        )

        approval_message = (
                f"Great news! Your delivery date change request has been approved. "
                f"Your new delivery date is {_format_date(requested_date)}."  # ← use stripped variable
                )

    elif req["type"] == "address_change":
        new_address = (
            f"{req['requested_address']}, {req['requested_city']}, "
            f"{req['requested_state']} - {req['requested_pincode']}"
        )

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
                "full_address": req["requested_address"],
                "city":         req["requested_city"],
                "state":        req["requested_state"],
                "pincode":      req["requested_pincode"],
                "order_id":     req["order_id"],
            }
        )

        approval_message = (
            f"Great news! Your delivery address change request has been approved. "
            f"Your new delivery address is {new_address}."
        )

    elif req["type"] == "return_request":
        items = json.loads(req["items"]) if req["items"] else []
        items_str = ", ".join(items) if items else "your items"

        # Generate a simple RMA number from the request_id
        rma_number = f"RMA-{request_id[:8].upper()}"

        approval_message = (
            f"Great news! Your return request has been approved. "
            f"Your RMA number is {rma_number}. "
            f"Please include this number on your return package. "
            f"Return shipping will be covered by "
            f"{'Leafy' if req['return_shipping_covered_by'] == 'leafy' else 'you (the customer)'}. "
            f"Your refund via {req['refund_method'].replace('_', ' ')} "
            f"will be processed within 5–7 business days of receiving the return."
        )
    
    elif req["type"] == "missing_item":
        items     = json.loads(req["reported_items"]) if req["reported_items"] else []
        items_str = ", ".join(items) if items else "the reported item(s)"

        approval_message = (
            f"Good news! We have investigated your missing item report. "
            f"We will reship {items_str} to you within 3–5 business days. "
            f"You will receive a shipping confirmation once dispatched."
        )
    elif req["type"] == "cancellation_request":
        # Flip order status to 'Cancelled'.
        # We normalise the existing value first so both 'cancelled' and
        # 'Cancelled' rows get updated cleanly.
        await session.execute(
            text("""
                UPDATE orders
                SET    order_status = 'Cancelled'
                WHERE  order_id = :order_id
                  AND  LOWER(order_status) NOT IN ('cancelled', 'delivered', 'shipped')
            """),
            {"order_id": req["order_id"]}
        )
 
        approval_message = (
            f"Your cancellation request for order "
            f"#{str(req['order_id'])[-8:].upper()} has been approved. "
            "Your refund will be returned to your original payment method "
            "within 3–5 business days. "
            "No further action is needed on your end."
        )

    else:
        # Fallback for any future request types
        approval_message = "Your request has been approved."

    # ── Commit + notify (same for all types) ─────────────────────────────────

    await session.commit()

    session_id = req["session_id"] or ""
    if not session_id:
        logger.warning(
            f"[NOTIFY] No session_id for request={request_id} type={req['type']} "
            f"— cannot deliver approval notification"
        )
    else:
        try:
            await conversations.append_notification(
                session_id = session_id,
                message    = approval_message,
                status     = "approved",
            )
            delivered = await ws_manager.notify_session(
                session_id = session_id,
                payload    = {
                    "type":    "request_resolved",
                    "status":  "approved",
                    "message": approval_message,
                }
            )
            logger.info(
                f"[NOTIFY] Approval notification — request={request_id} "
                f"session={session_id} ws_delivered={delivered}"
            )
        except Exception as e:
            logger.error(
                f"[NOTIFY] Failed to send approval notification "
                f"request={request_id} session={session_id}: {e}"
            )

    return {"status": "approved", "request_id": request_id, "note": note}

async def _pg_reject_request(
    request_id:    str,
    note:          str | None,
    admin_email:   str,
    session:       AsyncSession,
    conversations: ConversationStore,
) -> dict:
    result = await session.execute(
        text("SELECT * FROM pending_requests WHERE id = :id"),
        {"id": request_id}
    )
    req = result.mappings().first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found.")
    if req["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Request is already '{req['status']}'.")

    now = datetime.now(timezone.utc)

    await session.execute(
        text("""
            UPDATE pending_requests
            SET status          = 'rejected',
                resolved_at     = :resolved_at,
                resolved_by     = :resolved_by,
                resolution_note = :note
            WHERE id = :id
        """),
        {"id": request_id, "resolved_at": now, "resolved_by": admin_email, "note": note}
    )
    await session.commit()

    if req["type"] == "address_change":
        rejection_message = (
        "Unfortunately your delivery address change request could not be approved. "
        f"Reason: {note or 'No reason provided'}."
    )
    elif req["type"] == "return_request":
        rejection_message = (
        "Unfortunately your return request could not be approved. "
        f"Reason: {note or 'No reason provided'}. "
        "If you have further questions please contact our support team."
    )
    elif req["type"] == "missing_item":
        rejection_message = (
            "We have investigated your missing item report and were unable to verify "
            "the claim at this time. "
            f"Reason: {note or 'No reason provided'}. "
            "Please contact our support team if you have further questions."
        )
    elif req["type"] == "cancellation_request":
        rejection_message = (
            "Unfortunately your cancellation request could not be approved. "
            f"Reason: {note or 'No reason provided'}. "
            "If your order has already shipped, please wait for it to arrive "
            "and then initiate a return through our support chat."
        )
    else:
        rejection_message = (
        "Unfortunately your delivery date change request could not be approved. "
        f"Reason: {note or 'No reason provided'}."
    )

    session_id = req["session_id"] or ""
    if not session_id:
        logger.warning(
            f"[NOTIFY] No session_id for request={request_id} type={req['type']} "
            f"— cannot deliver rejection notification"
        )
    else:
        try:
            await conversations.append_notification(
                session_id = session_id,
                message    = rejection_message,
                status     = "rejected",
            )
            delivered = await ws_manager.notify_session(
                session_id = session_id,
                payload    = {
                    "type":    "request_resolved",
                    "status":  "rejected",
                    "message": rejection_message,
                }
            )
            logger.info(
                f"[NOTIFY] Rejection notification — request={request_id} "
                f"session={session_id} ws_delivered={delivered}"
            )
        except Exception as e:
            logger.error(
                f"[NOTIFY] Failed to send rejection notification "
                f"request={request_id} session={session_id}: {e}"
            )

    return {"status": "rejected", "request_id": request_id, "note": note}


async def _pg_get_stats(session: AsyncSession) -> dict:
    result = await session.execute(
        text("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'pending')  AS pending,
                COUNT(*) FILTER (WHERE status = 'approved') AS approved,
                COUNT(*) FILTER (WHERE status = 'rejected') AS rejected,
                COUNT(*)                                     AS total
            FROM pending_requests
        """)
    )
    row = result.mappings().first()
    return {
        "pending":  row["pending"],
        "approved": row["approved"],
        "rejected": row["rejected"],
        "total":    row["total"],
    }

@router.get("/escalations")
async def get_escalations(
    status:       str          = "open",
    session:      AsyncSession = Depends(get_pg_session),
    _:            dict         = Depends(get_current_admin),
):
    result = await session.execute(
        text("""
            SELECT
                e.id,
                e.reason,
                e.status,
                e.priority,
                e.order_id,
                e.customer_note,
                e.created_at,
                e.resolved_at,
                e.resolved_by,
                e.resolution_note,
                u.name,
                u.surname,
                u.email,
                u.loyalty_tier
            FROM escalations e
            LEFT JOIN users u ON u.id = e.user_id
            WHERE e.status = :status
            ORDER BY e.priority DESC, e.created_at ASC
            LIMIT 50
        """),
        {"status": status}
    )
    rows = result.mappings().all()

    escalations = []
    for row in rows:
        escalations.append({
            "id":              row["id"],
            "reason":          row["reason"],
            "status":          row["status"],
            "priority":        row["priority"],
            "order_id":        row["order_id"],
            "customer_note":   row["customer_note"],
            "created_at":      str(row["created_at"]) if row["created_at"] else None,
            "resolved_at":     str(row["resolved_at"]) if row["resolved_at"] else None,
            "resolved_by":     row["resolved_by"],
            "resolution_note": row["resolution_note"],
            "customer": {
                "name":        f"{row['name']} {row['surname']}",
                "email":       row["email"],
                "loyaltyTier": row["loyalty_tier"],
            },
        })
    return {"escalations": escalations}


@router.post("/escalations/{escalation_id}/resolve")
async def resolve_escalation(
    escalation_id: str,
    body:          ResolutionBody,
    session:       AsyncSession = Depends(get_pg_session),
    admin:         dict         = Depends(get_current_admin),
):
    now = datetime.now(timezone.utc)
    await session.execute(
        text("""
            UPDATE escalations
            SET status          = 'resolved',
                resolved_at     = :resolved_at,
                resolved_by     = :resolved_by,
                resolution_note = :note
            WHERE id = :id
        """),
        {
            "id":          escalation_id,
            "resolved_at": now,
            "resolved_by": admin["email"],
            "note":        body.note,
        }
    )
    await session.commit()
    return {"status": "resolved", "escalation_id": escalation_id}

# ── Routes ────────────────────────────────────────────────────────────────────



@router.get("/requests/stats")
async def get_stats(
    current_user: dict         = Depends(get_current_admin),
    session:      AsyncSession = Depends(get_pg_session),
):
    if settings.db_tool_mode == "postgres":
        return await _pg_get_stats(session)

    from backend.database import get_db
    db       = get_db()
    pending  = await db.pending_requests.count_documents({"status": "pending"})
    approved = await db.pending_requests.count_documents({"status": "approved"})
    rejected = await db.pending_requests.count_documents({"status": "rejected"})
    return {
        "pending":  pending,
        "approved": approved,
        "rejected": rejected,
        "total":    pending + approved + rejected,
    }


@router.get("/requests")
async def get_pending_requests(
    status:       str         = "pending",
    current_user: dict        = Depends(get_current_admin),
    session:      AsyncSession = Depends(get_pg_session),
):
    if settings.db_tool_mode == "postgres":
        requests = await _pg_get_pending_requests(status, session)
        return {"requests": requests, "total": len(requests)}

    # Mongo
    from bson import ObjectId
    from backend.database import get_db
    db     = get_db()
    cursor = db.pending_requests.find({"status": status}).sort("created_at", -1).limit(50)

    requests = []
    async for req in cursor:
        serialized = _serialize_request(req)
        try:
            order = await db.orders.find_one(
                {"_id": ObjectId(str(req["order_id"]))},
                {"status": 1, "products": 1, "shipping_address": 1, "estimated_destination_date": 1}
            )
            if order:
                serialized["order"] = {
                    "status":           order.get("status"),
                    "address":          order.get("shipping_address"),
                    "products":         [p.get("name", "Unknown") for p in order.get("products", [])[:3]],
                    "current_delivery": order["estimated_destination_date"].isoformat()
                                        if isinstance(order.get("estimated_destination_date"), datetime)
                                        else None,
                }
        except Exception:
            pass
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
    body:          ResolutionBody    = ResolutionBody(),
    current_user:  dict              = Depends(get_current_admin),
    conversations: ConversationStore = Depends(get_conversations),
    session:       AsyncSession      = Depends(get_pg_session),
):
    if settings.db_tool_mode == "postgres":
        return await _pg_approve_request(
            request_id    = request_id,
            note          = body.note,
            admin_email   = current_user.get("email"),
            session       = session,
            conversations = conversations,
        )

    # Mongo
    from bson import ObjectId
    from backend.database import get_db
    db  = get_db()
    now = datetime.now(timezone.utc)
    try:
        rid = ObjectId(request_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request ID.")
    req = await db.pending_requests.find_one({"_id": rid})
    if not req:
        raise HTTPException(status_code=404, detail="Request not found.")
    if req["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Request is already '{req['status']}'.")
    await db.pending_requests.update_one(
        {"_id": rid},
        {"$set": {
            "status":          "approved",
            "resolved_at":     now,
            "resolved_by":     current_user.get("email"),
            "resolution_note": body.note,
        }}
    )
    update_fields = {
        "delivery_date_change_request.status":      "approved",
        "delivery_date_change_request.resolved_at": now,
    }
    if req["type"] == "date_change":
        update_fields["estimated_destination_date"] = req["requested_value"]
    await db.orders.update_one({"_id": req["order_id"]}, {"$set": update_fields})
    approval_message = (
        f"Great news! Your delivery date change request has been approved. "
        f"Your new delivery date is {_format_date(req['requested_value'])}."
    )
    session_id = str(req.get("session_id", ""))
    if session_id:
        await conversations.append_notification(session_id=session_id, message=approval_message, status="approved")
    await ws_manager.notify_session(session_id=session_id, payload={"type": "request_resolved", "status": "approved", "message": approval_message})
    return {"status": "approved", "request_id": request_id, "note": body.note}


@router.post("/requests/{request_id}/reject")
async def reject_request(
    request_id:    str,
    body:          ResolutionBody    = ResolutionBody(),
    current_user:  dict              = Depends(get_current_admin),
    conversations: ConversationStore = Depends(get_conversations),
    session:       AsyncSession      = Depends(get_pg_session),
):
    if settings.db_tool_mode == "postgres":
        return await _pg_reject_request(
            request_id    = request_id,
            note          = body.note,
            admin_email   = current_user.get("email"),
            session       = session,
            conversations = conversations,
        )

    # Mongo
    from bson import ObjectId
    from backend.database import get_db
    db  = get_db()
    now = datetime.now(timezone.utc)
    try:
        rid = ObjectId(request_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request ID.")
    req = await db.pending_requests.find_one({"_id": rid})
    if not req:
        raise HTTPException(status_code=404, detail="Request not found.")
    if req["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Request is already '{req['status']}'.")
    await db.pending_requests.update_one(
        {"_id": rid},
        {"$set": {
            "status":          "rejected",
            "resolved_at":     now,
            "resolved_by":     current_user.get("email"),
            "resolution_note": body.note,
        }}
    )
    await db.orders.update_one(
        {"_id": req["order_id"]},
        {"$set": {
            "delivery_date_change_request.status":          "rejected",
            "delivery_date_change_request.resolved_at":    now,
            "delivery_date_change_request.resolution_note": body.note,
        }}
    )
    rejection_message = (
        f"Unfortunately your delivery date change request could not be approved. "
        f"Reason: {body.note or 'No reason provided'}."
    )
    session_id = str(req.get("session_id", ""))
    if session_id:
        await conversations.append_notification(session_id=session_id, message=rejection_message, status="rejected")
    await ws_manager.notify_session(session_id=session_id, payload={"type": "request_resolved", "status": "rejected", "message": rejection_message})
    return {"status": "rejected", "request_id": request_id, "note": body.note}