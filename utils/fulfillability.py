"""
utils/fulfillability.py

Checks whether the current inventory can support a given order,
and computes a composite priority score.

WHY THIS EXISTS
--------------
The original priority was purely date-based: same-day = urgent, tomorrow = high,
anything else = normal.  That logic ignores two things the head flagged:
  1. Can we actually produce this order with current stock?
  2. Are any of the required materials below reorder level (lead-time risk)?

This module returns a structured result that answers both questions,
which is then displayed in the Order Intake tab when an order is parsed,
and stored on every order in the queue.

PRIORITY TIERS (in descending urgency)
---------------------------------------
critical : Same-day delivery AND materials cannot be fulfilled — needs
           immediate management decision (call client or call supplier).
urgent   : Same-day delivery AND materials are available — dispatch now.
high     : Next-day delivery, OR materials are insufficient (regardless of
           date) — requires planning action today.
normal   : Future delivery AND materials available — no immediate action.
watch    : Materials are available but one or more required materials is
           between the reorder level and 120% of the reorder level — worth
           monitoring, may cause a problem later in the week.
"""

from datetime import datetime
from typing import Dict, List, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# CORE FULFILLABILITY CHECK
# ─────────────────────────────────────────────────────────────────────────────

def check_fulfillability(
    grade: str,
    volume_m3: float,
    inventory: List[Dict],
) -> Dict:
    """
    Checks whether current inventory can supply a concrete order.

    For each material, we calculate:
        needed = consumption_per_m3_{grade} × volume_m3

    If needed > on_hand for any material, the order cannot be fulfilled
    from current stock.  We also flag materials that are available for
    this order but whose on_hand is below 120% of the reorder level —
    these are "low stock" warnings that don't block this order but signal
    a risk for upcoming orders.

    Returns a dict with:
        can_fulfill   : bool
        shortage_items: list of dicts describing each shortage
        low_stock_warnings: list of materials that are OK for this order
                           but approaching reorder level
        summary       : human-readable one-line status string
    """
    shortage_items = []
    low_stock_warnings = []
    available_items = []

    for item in inventory:
        rate = item.get(f"consumption_per_m3_{grade}", 0)
        if rate == 0:
            continue  # this material isn't used in this grade

        needed      = rate * volume_m3
        on_hand     = item["on_hand"]
        unit        = item["unit"]
        reorder_lvl = item["reorder_level"]
        material    = item["material"]

        if needed > on_hand:
            shortage_items.append({
                "material": material,
                "needed":   round(needed, 2),
                "on_hand":  on_hand,
                "shortfall": round(needed - on_hand, 2),
                "unit":     unit,
                "lead_time_days": item.get("lead_time_days", "?"),
                "supplier": item.get("supplier", "?"),
            })
        else:
            remaining_after = on_hand - needed
            if remaining_after < reorder_lvl * 1.2:
                low_stock_warnings.append({
                    "material": material,
                    "remaining_after_this_order": round(remaining_after, 2),
                    "reorder_level": reorder_lvl,
                    "unit": unit,
                    "lead_time_days": item.get("lead_time_days", "?"),
                })
            available_items.append(material)

    can_fulfill = len(shortage_items) == 0

    if can_fulfill and not low_stock_warnings:
        summary = f"✅ Can fulfill — all materials available for {volume_m3} m³ of {grade}"
    elif can_fulfill and low_stock_warnings:
        warn_names = ", ".join(w["material"].split("(")[0].strip() for w in low_stock_warnings)
        summary = f"⚠️ Can fulfill but low stock after this order: {warn_names}"
    else:
        shortage_names = ", ".join(
            f"{s['material'].split('(')[0].strip()} (need {s['needed']:.1f}, have {s['on_hand']:.1f} {s['unit']})"
            for s in shortage_items
        )
        summary = f"❌ Cannot fulfill — insufficient: {shortage_names}"

    return {
        "can_fulfill":         can_fulfill,
        "shortage_items":      shortage_items,
        "low_stock_warnings":  low_stock_warnings,
        "summary":             summary,
    }


# ─────────────────────────────────────────────────────────────────────────────
# COMPOSITE PRIORITY CALCULATION
# ─────────────────────────────────────────────────────────────────────────────

def compute_priority(
    delivery_date_str: str,
    fulfillability: Dict,
    today: datetime = None,
    delivery_time_str: str = None,   # NEW: "HH:MM" — enables intra-day overdue detection
) -> Tuple[str, str]:
    """
    Returns (priority_level, priority_reason) as a tuple.

    The priority_reason is the explanation that appears in the order queue
    so dispatchers and plant managers understand *why* an order was flagged,
    not just *that* it was flagged.

    Priority levels: critical → urgent → high → watch → normal

    WHY delivery_time_str MATTERS
    ------------------------------
    The original version stripped the time component from both "now" and the
    delivery datetime (resetting both to midnight), which meant it could only
    compare at day-level granularity.  The consequence was that an order for
    "Thursday 08:00" checked at 12:30 on Thursday would compute days_until=0
    and be classified as "Urgent" rather than "Overdue" — because both
    timestamps normalised to Thursday 00:00 and the difference was zero days.

    The fix: use datetime.now() without stripping the time, and parse the
    delivery time if provided.  Now the comparison is in minutes, not days,
    so an order whose delivery window passed three hours ago correctly shows
    as overdue even when the calendar date is still today.
    """
    now = today if today is not None else datetime.now()

    # Parse the delivery date; if a time is also provided, combine them.
    # This gives us a full delivery_dt (e.g. 2026-05-21 08:00) to compare
    # against the actual current moment rather than midnight-to-midnight.
    try:
        delivery_dt = datetime.strptime(delivery_date_str, "%Y-%m-%d")
        if delivery_time_str:
            try:
                t = datetime.strptime(delivery_time_str, "%H:%M")
                delivery_dt = delivery_dt.replace(hour=t.hour, minute=t.minute)
            except ValueError:
                pass  # keep date-only if time string is malformed
        else:
            # No specific time given — treat the delivery window as open until
            # end of day (23:59) so that "deliver Friday" isn't flagged overdue
            # at 00:01 on Friday morning. Only mark overdue once the day is over.
            delivery_dt = delivery_dt.replace(hour=23, minute=59)
    except (ValueError, TypeError):
        delivery_dt = now  # safe-fail: treat unparseable dates as now

    # Minutes-level difference: negative means the delivery window has passed.
    # Using total_seconds() / 60 rather than .days so that "Thursday 08:00
    # checked at 12:30 Thursday" gives -270 minutes (overdue), not 0 days (same day).
    minutes_until = (delivery_dt - now).total_seconds() / 60
    days_until    = (delivery_dt.date() - now.date()).days
    can_fulfill   = fulfillability["can_fulfill"]

    if minutes_until < 0:
        # The delivery window has already passed — overdue regardless of
        # whether it's still the same calendar day.
        if days_until < 0:
            return ("high", f"⏰ Overdue — delivery was {abs(days_until)} day(s) ago")
        else:
            # Same calendar day but time has passed (e.g. ordered for 08:00, now 12:30)
            hours_ago = abs(int(minutes_until // 60))
            mins_ago  = abs(int(minutes_until % 60))
            time_str  = f"{hours_ago}h {mins_ago}m ago" if hours_ago else f"{mins_ago}m ago"
            return ("high", f"⏰ Overdue — delivery window passed {time_str} (was {delivery_time_str or 'this morning'})")

    if days_until == 0 and not can_fulfill:
        return ("critical",
                "🚨 Same-day delivery but materials insufficient — call client and supplier immediately")

    if days_until == 0 and can_fulfill:
        return ("urgent", "🔴 Same-day delivery — dispatch immediately")

    if days_until == 1 and not can_fulfill:
        shortfall = fulfillability["shortage_items"][0]
        lead = shortfall.get("lead_time_days", "?")
        return ("critical",
                f"🚨 Next-day delivery but {shortfall['material'].split('(')[0].strip()} "
                f"insufficient (lead time: {lead} day(s)) — cannot fulfill on time")

    if days_until == 1:
        if fulfillability["low_stock_warnings"]:
            return ("high",
                    "🟠 Tomorrow delivery — materials available but stock will be very low after this order")
        return ("high", "🟠 Next-day delivery — plan dispatch today")

    if not can_fulfill:
        shortfall = fulfillability["shortage_items"][0]
        lead = shortfall.get("lead_time_days", "?")
        return ("high",
                f"🟠 Materials insufficient — order {shortfall['material'].split('(')[0].strip()} "
                f"({shortfall['shortfall']:.1f} {shortfall['unit']} short, {lead}d lead time)")

    if fulfillability["low_stock_warnings"]:
        warn = fulfillability["low_stock_warnings"][0]
        return ("watch",
                f"👁 Materials OK for this order but {warn['material'].split('(')[0].strip()} "
                f"will drop near reorder level after fulfillment")

    return ("normal", f"✅ Future delivery ({days_until}d away) — materials available")


# ─────────────────────────────────────────────────────────────────────────────
# STATUS VOCABULARY
# ─────────────────────────────────────────────────────────────────────────────

# Standardised display labels for order statuses.
# The old terms (pending, confirmed, in_production) were ambiguous —
# "pending" in particular could mean "waiting for anything" with no clear
# definition of what it's waiting for.
STATUS_DISPLAY = {
    "pending":       "Pending Review",
    "confirmed":     "Confirmed",
    "in_production": "In Production",
    "dispatched":    "Dispatched",
    "delivered":     "Delivered",
    "done":          "Delivered",
    "on_hold":       "On Hold",
}

STATUS_EMOJI = {
    "pending":       "📋",
    "confirmed":     "✅",
    "in_production": "⚙️",
    "dispatched":    "🚛",
    "delivered":     "📍",
    "done":          "📍",
    "on_hold":       "⏸️",
}

PRIORITY_DISPLAY = {
    "critical": "🚨 Critical",
    "urgent":   "🔴 Urgent",
    "high":     "🟠 High",
    "watch":    "👁 Watch",
    "normal":   "⚪ Normal",
}

PRIORITY_COLOR = {
    "critical": "#ef4444",
    "urgent":   "#f97316",
    "high":     "#f59e0b",
    "watch":    "#3b82f6",
    "normal":   "#6b7280",
}
