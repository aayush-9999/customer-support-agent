# Orders & Delivery — Facts

## Order Status: What Each Means

| Status | Tell the customer |
|---|---|
| Processing / In process | Order is being prepared — not shipped yet |
| Shipped / In Transit | On its way. Estimated delivery: [date from order details] |
| Out for Delivery | With local driver today |
| Delivered | Carrier confirms delivery on [date]. If not received, wait 24h then contact us |
| Delayed | Carrier delay. If no update in 7 business days (domestic) / 14 (international), Leafy files a claim |
| Returned to Sender | Package came back. Confirm address with customer, reship |

## Tracking
- Tracking number emailed when order ships
- 24–48h tracking lag after shipping notification is normal
- No update for 7+ days (domestic) or 14+ days (international) → escalate, Leafy files carrier claim

## Address Changes
- Address change possible only while status is "In process" or "Ready for delivery"
- Once shipped: cannot redirect. If returned to Leafy, reshipped at no charge

## Delivery Date Changes
- Tool decides automatically based on warehouse date — do not promise outcomes before calling it
- Outcomes: rejected (before warehouse date), pending_approval, already_pending, terminal_status

## Cannot Do
- Modify order contents after placement
- Upgrade shipping speed or expedite
- Guarantee specific delivery dates beyond what the data shows

## Edge Cases
- Missing item: check order details first — some multi-item orders ship in separate packages. If genuinely missing: escalate with order ID and missing item name.
- Wrong item received: Leafy covers return shipping. Confirm expected items via get_order_details, initiate return with reason "Wrong item received."
- No shipping confirmation email: check get_order_details for tracking number. Ask customer to check spam.