# Leafy FAQs — Orders & Delivery

## "Where is my order?" / "When will it arrive?" / Anything order-related

ALWAYS follow this flow — do not shortcut it:
1. Call `get_order_history(email)` first to see all orders.
2. If 1 active order → proceed with it.
   If multiple → list them in plain language and ask which one.
3. Once the customer confirms → call `get_order_details(order_id)`.
4. Report the status using the table below. Include estimated delivery date if available.

Never jump straight to `get_order_details`. Never ask the customer for an order ID.

| Status | What to tell the customer |
|---|---|
| Processing / In process | "Your order is being prepared — it hasn't shipped yet." |
| Shipped / In Transit | "Your order is on its way. Estimated delivery: [date]." |
| Out for Delivery | "Good news — your order is with the local driver today." |
| Delivered | "Tracking shows it was delivered on [date]. If you haven't received it, wait 24 hours and let us know." |
| Delayed | "There's a carrier delay. If there's no update in 7 business days we'll file a claim." |
| Returned to Sender | "The package came back to us. Let's confirm your address and get it reshipped." |

---

## "I want to change my delivery date" / "Can I get it sooner/later?"

Flow:
1. If the customer hasn't said which order, call `get_order_history` first and confirm.
2. Call `change_delivery_date(order_id, requested_date)`.
3. The tool decides automatically:
   - **Rejected** (date is before or on the warehouse date) → explain why and give the earliest possible date.
   - **Pending approval** → tell the customer: "Your request has been sent to our team. You'll hear back within 24 hours — we'll notify you here once it's reviewed."
   - **Already pending** → tell them one request is already under review, ask them to wait.
   - **Terminal status** → the order is already delivered/cancelled, can't change anything.

---

## "Can I change my delivery address?"

Use `change_delivery_address(order_id, ...)`.
- Works only when order status is "In process" or "Ready for delivery".
- If already shipped: "Once an order is picked up from our warehouse we can't redirect it. If it comes back to us, we'll reship at no charge."

---

## "My tracking hasn't updated in days"

- Domestic: 7+ business days with no update → Leafy files a carrier claim. Escalate.
- International: 14+ business days → same.
- Recent orders (< 48h after shipping notification): tracking lag is normal, reassure the customer.

---

## "I never received a shipping confirmation email"

Check `get_order_details` — if status is "Shipped", the tracking number is in the system.
Ask the customer to check spam. Offer to resend the notification to their email on file.

---

## "Can I change or add items to my order?"

No — orders cannot be modified once placed.
Options: cancel (only if still "Processing") and reorder, or place a separate order.

---

## "My order arrived but something is missing"

Check `get_order_details` — confirm the expected items. Some multi-item orders ship in separate packages.
If an item is genuinely missing: escalate immediately with the order ID and missing item name.

---

## "I received the wrong item"

This qualifies as "Wrong item received" — return shipping is covered by Leafy.
1. Confirm with `get_order_details` what was supposed to be in the order.
2. Tell the customer to initiate a return via chat with reason "Wrong item received".
3. A free return label will be provided.
4. Offer a replacement or refund once the return is initiated.