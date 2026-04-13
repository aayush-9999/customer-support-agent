# Change Order Policy — Size & Colour

## Overview
Leafy allows customers to request size and/or colour changes for items in an order.  
All changes are subject to **stock availability** and require **admin approval**.  

No changes are applied instantly — every request is reviewed before confirmation.

---

## How Item Changes Work

- Customers must provide:
  - Order ID  
  - Item name  
  - Desired size and/or colour  

- A **change request is created** and sent for admin approval  
- Customers are notified once the request is reviewed (within 24 hours)

---

## Order Status-Based Handling

### Orders in "Processing" or "In process"
- Stock is checked directly from the **product catalogue**
- If the requested variant exists and is in stock:
  - A **pending approval request** is created
- If not available:
  - The request is rejected or marked out of stock

---

### Orders in Any Other Active Status
(e.g. Ready for Delivery, In Transit, etc.)

- Stock is checked from **warehouse inventory**
- If available in any warehouse:
  - A **pending approval request** is created
- If not available:
  - The request is rejected

---

### Orders in Final States

For orders with status:
- Delivered  
- Completed  
- Cancelled  

❌ Item changes are **not allowed**

---

## Stock Validation Rules

A request is only considered if:

- The requested **size/colour variant exists**
- The variant has **available stock (> 0)**

Otherwise:
- The request is rejected or marked as out of stock

---

## Pending Request Handling

- Only **one active (pending) change request per order** is allowed
- If a request is already pending:
  - A new request cannot be created until the previous one is resolved

---

## What Happens After Request Submission

- A **pending request** is created in the system
- The order is updated with:
  - Request details
  - Status note: *"Item Change Pending Approval"*
- Admin team reviews and approves/rejects the request

---

## What Is Not Possible

- Instantly modifying item size or colour  
- Bypassing admin approval  
- Creating multiple simultaneous change requests for the same order  
- Changing items in Delivered / Completed / Cancelled orders  

---

## Important Notes

- Customers must confirm the desired size and/or colour before submitting the request  
- Availability is checked in real-time from:
  - Product catalogue (early stage)
  - Warehouse inventory (later stage)

---

## Last Updated
**2026**

Policy applies to all orders placed on **leafy.store**