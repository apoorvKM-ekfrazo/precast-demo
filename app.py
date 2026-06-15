"""
app.py — Apex Precast AI Pilot
Run: streamlit run app.py

5 tabs:
  🏭 Overview         — KPI dashboard
  📦 Inventory        — stock levels + batch capacity
  📋 Order Intake     — order queue + AI parser
  🚛 Smart Dispatch   — truck scheduler + Gantt + map
  🔍 Traceability     — batch lookup with full journey
  💬 AI Copilot       — natural language plant queries
"""

import json
import math
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import folium
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from streamlit_folium import st_folium

# Load .env file (GROQ_API_KEY, ORS_API_KEY) — silent no-op if no .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    for key, value in st.secrets.items():
        if isinstance(value, str):
            os.environ.setdefault(key, value)
except Exception:
    pass

# ── import our utilities ──────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from utils.scheduler import run_dispatch, haversine_km, assess_slump_risk
from utils.llm_client import parse_order, copilot_query
from utils.ors_routing import fetch_all_dispatch_routes
from utils.fulfillability import (
    check_fulfillability, compute_priority,
    STATUS_DISPLAY, STATUS_EMOJI, PRIORITY_DISPLAY, PRIORITY_COLOR
)

# ── page config ───────────────────────────────────────────────────────
st.set_page_config(
    page_title="Apex Precast AI",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ─────────────────────────────────────────────────────────────────────
# DATA LOADER
# ─────────────────────────────────────────────────────────────────────
DATA_DIR = Path("data")

@st.cache_data
def load_data():
    with open(DATA_DIR / "inventory.json") as f: inventory = json.load(f)
    with open(DATA_DIR / "orders.json")    as f: orders    = json.load(f)
    with open(DATA_DIR / "trucks.json")    as f: trucks    = json.load(f)
    with open(DATA_DIR / "batches.json")   as f: batches   = json.load(f)
    with open(DATA_DIR / "sites.json")     as f: sites     = json.load(f)
    with open(DATA_DIR / "plant.json")     as f: plant     = json.load(f)
    return inventory, orders, trucks, batches, sites, plant

inventory, orders, trucks, batches, sites, plant = load_data()

# Augment orders with live dispatch if not already cached in session
# Enrich every order with live fulfillability + composite priority
def enrich_order(order, inventory):
    """Add fulfillability check and composite priority to an order dict.

    The delivery_time is passed through to compute_priority so that intra-day
    overdue detection works correctly — e.g. an order for "Thursday 08:00"
    checked at 12:30 on Thursday is flagged Overdue, not Urgent.
    """
    grade  = order.get("grade", "C40")
    volume = order.get("volume_m3", 8.0)
    deliv  = order.get("delivery_date", "")
    dtime  = order.get("delivery_time", None)   # "HH:MM" or None
    result = check_fulfillability(grade, volume, inventory)
    priority, reason = compute_priority(deliv, result, delivery_time_str=dtime)
    return {**order,
            "fulfillability": result,
            "priority": priority,
            "priority_reason": reason}

if "dispatch_result" not in st.session_state:
    st.session_state.dispatch_result = run_dispatch(orders, trucks)

if "added_orders" not in st.session_state:
    st.session_state.added_orders = []

# ─────────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────────
with st.sidebar:
    # Pure CSS logo banner — no external image URL, so it always renders reliably.
    # via.placeholder.com requires an outbound HTTP call that silently fails on
    # many networks, producing the broken image icon.
    st.markdown("""
    <div style='background:linear-gradient(135deg,#1a3a5c,#0f5e3e);
                padding:14px 16px;border-radius:10px;margin-bottom:4px;
                border-left:4px solid #10b981'>
        <div style='color:white;font-size:1.05rem;font-weight:700;
                    letter-spacing:0.02em'>🏗️ Apex Precast</div>
        <div style='color:#6ee7b7;font-size:0.78rem;margin-top:3px'>
            AI Plant Intelligence · Pilot v0.1</div>
        <div style='color:#94a3b8;font-size:0.72rem;margin-top:1px'>
            Athlone, Co. Westmeath</div>
    </div>
    """, unsafe_allow_html=True)
    st.divider()

    groq_key = st.text_input(
        "Groq API Key (optional)",
        value="",
        placeholder="✓ Configured" if os.environ.get("GROQ_API_KEY") else "Paste key here...",
        help="Get a free key at console.groq.com"
    )
    if groq_key:
        os.environ["GROQ_API_KEY"] = groq_key
    # ADD THIS LINE ↓
    if not groq_key:
        groq_key = os.environ.get("GROQ_API_KEY", "")

    

    ors_key = st.text_input(
        "ORS API Key (optional)",
        value="",
        placeholder="✓ Configured" if os.environ.get("ORS_API_KEY") else "Paste key here...",
        help="Enables real road routes on dispatch map. Free at openrouteservice.org"
    )
    if ors_key:
        os.environ["ORS_API_KEY"] = ors_key

    if not ors_key:
        ors_key = os.environ.get("ORS_API_KEY", "")

    st.divider()
    st.markdown("**Today**")
    st.markdown(f"`{datetime.now().strftime('%A, %d %B %Y')}`")

    st.divider()
    st.markdown("**Fleet Status**")
    avail  = sum(1 for t in trucks if t["status"] == "available")
    onroad = sum(1 for t in trucks if t["status"] == "en_route")
    maint  = sum(1 for t in trucks if t["status"] == "maintenance")
    st.markdown(f"🟢 Available: **{avail}**")
    st.markdown(f"🟡 En Route: **{onroad}**")
    st.markdown(f"🔴 Maintenance: **{maint}**")

    st.divider()
    if st.button("🔄 Regenerate Data", use_container_width=True):
        import subprocess
        subprocess.run(["python", "data_generator.py"], capture_output=True)
        st.cache_data.clear()
        st.session_state.pop("dispatch_result", None)
        st.success("Data refreshed!")
        st.rerun()

# ─────────────────────────────────────────────────────────────────────
# TABS
# ─────────────────────────────────────────────────────────────────────
tab_overview, tab_inventory, tab_orders, tab_dispatch, tab_trace, tab_copilot = st.tabs([
    "🏭 Overview",
    "📦 Inventory",
    "📋 Order Intake",
    "🚛 Smart Dispatch",
    "🔍 Traceability",
    "💬 AI Copilot"
])

# ═════════════════════════════════════════════════════════════════════
# TAB 1 — OVERVIEW
# ═════════════════════════════════════════════════════════════════════
with tab_overview:
    st.title("🏭 Plant Overview — Apex Precast")
    st.caption(f"Athlone, Co. Westmeath  ·  {datetime.now().strftime('%d %b %Y, %H:%M')}")
    st.divider()

    # KPI row
    all_orders  = orders + st.session_state.added_orders
    pending_vol = sum(o["volume_m3"] for o in all_orders if o["status"] in ("pending","confirmed"))
    low_stock   = sum(1 for i in inventory if i["on_hand"] <= i["reorder_level"] * 1.2)
    qa_fail     = sum(1 for b in batches if b["qa"]["overall_qa"] == "FAIL")
    urgent_cnt  = sum(1 for o in all_orders if o.get("priority") == "urgent")

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Total Orders",      len(all_orders), delta=f"+{len(st.session_state.added_orders)} new")
    k2.metric("Volume Pending",    f"{pending_vol:.0f} m³")
    k3.metric("Trucks Available",  avail, delta=f"{onroad} en route")
    k4.metric("Low Stock Alerts",  low_stock, delta_color="inverse", delta=None)
    k5.metric("QA Failures (14d)", qa_fail)

    if urgent_cnt:
        st.warning(f"⚠️  **{urgent_cnt} urgent orders** require same-day dispatch — check the Dispatch tab.")

    st.divider()

    col_l, col_r = st.columns(2)

    with col_l:
        st.subheader("Orders by Status")
        status_counts = pd.Series([o["status"] for o in all_orders]).value_counts().reset_index()
        status_counts.columns = ["Status", "Count"]
        color_map = {"pending": "#f59e0b", "confirmed": "#3b82f6", "in_production": "#8b5cf6"}
        fig = px.bar(
            status_counts, x="Status", y="Count",
            color="Status", color_discrete_map=color_map,
            text="Count"
        )
        fig.update_layout(showlegend=False, height=280, margin=dict(t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)

    with col_r:
        st.subheader("Volume by Grade (m³)")
        grade_vol = {}
        for o in all_orders:
            grade_vol[o["grade"]] = grade_vol.get(o["grade"], 0) + o["volume_m3"]
        fig2 = px.pie(
            names=list(grade_vol.keys()),
            values=list(grade_vol.values()),
            color_discrete_sequence=["#3b82f6", "#10b981", "#f59e0b"]
        )
        fig2.update_layout(height=280, margin=dict(t=10, b=10))
        st.plotly_chart(fig2, use_container_width=True)

    st.divider()
    st.subheader("🗺️ Delivery Sites")
    m = folium.Map(location=[53.9069, -6.8092], zoom_start=8, tiles="CartoDB positron")
    folium.Marker(
        [53.9069, -6.8092],
        popup="Apex Precast Plant — Athlone",
        icon=folium.Icon(color="red", icon="industry", prefix="fa"),
        tooltip="🏭 Plant"
    ).add_to(m)
    for site in sites:
        site_orders = [o for o in all_orders if o.get("site_id") == site["id"]]
        vol = sum(o["volume_m3"] for o in site_orders)
        folium.CircleMarker(
            [site["lat"], site["lon"]],
            radius=6 + vol / 10,
            color="#3b82f6", fill=True, fill_opacity=0.7,
            popup=f"{site['name']}<br>{vol:.0f} m³ ordered",
            tooltip=site["name"]
        ).add_to(m)
    st_folium(m, height=380, use_container_width=True)


# ═════════════════════════════════════════════════════════════════════
# TAB 2 — INVENTORY
# ═════════════════════════════════════════════════════════════════════
with tab_inventory:
    st.title("📦 Inventory Intelligence")
    st.caption("Real-time stock visibility — aggregates, cement, admixtures")
    st.divider()

    # Grade selector for batch capacity calc
    col_grade, col_vol = st.columns([1, 3])
    selected_grade = col_grade.selectbox("Concrete Grade for capacity calc", ["C30", "C40", "C50"])
    batch_size     = col_vol.slider("Batch size (m³)", 4.0, 9.0, 8.0, 0.5)

    st.divider()

    # Build rows
    rows = []
    for item in inventory:
        key = f"consumption_per_m3_{selected_grade}"
        rate = item.get(key, 0)
        batches_possible = int(item["on_hand"] / rate / batch_size) if rate > 0 else 9999
        pct = (item["on_hand"] / item["max_capacity"]) * 100
        if item["on_hand"] <= item["reorder_level"]:
            rag = "🔴 REORDER NOW"
        elif item["on_hand"] <= item["reorder_level"] * 1.4:
            rag = "🟡 LOW"
        else:
            rag = "🟢 OK"

        rows.append({
            "Material": item["material"],
            "On Hand": f"{item['on_hand']:.1f} {item['unit']}",
            "Reorder At": f"{item['reorder_level']:.0f} {item['unit']}",
            f"Batches ({selected_grade})": batches_possible,
            "Capacity %": pct,
            "Status": rag,
            "Supplier": item["supplier"],
            "Lead Time": f"{item['lead_time_days']}d",
        })

    df = pd.DataFrame(rows)

    # Colour the status column
    st.dataframe(
        df.drop(columns=["Capacity %"]),
        use_container_width=True,
        hide_index=True,
        column_config={
            f"Batches ({selected_grade})": st.column_config.NumberColumn(
                f"Batches Possible ({selected_grade})",
                help=f"How many {batch_size}m³ {selected_grade} batches can we make before this material runs out"
            )
        }
    )

    st.divider()

    # Stock level gauge chart
    # ── WHY THE REWRITE ──────────────────────────────────────────────
    # The old approach drew each bar as a separate trace and then used
    # add_shape(x0=-0.4, x1=0.4) for every material.  The problem:
    # add_shape coordinates with xref="x" are interpreted as numeric
    # positions on the x-axis.  When bars are separate traces with
    # categorical x values, Plotly maps them all to axis position 0
    # (the first category) — so every threshold line collapsed onto the
    # first bar (OPC 53 Grade Cement).
    #
    # The fix uses a SINGLE go.Bar trace (all materials together) so
    # Plotly assigns deterministic integer positions 0,1,2…  Then we
    # add threshold markers as a go.Scatter trace with symbol="line-ew"
    # which draws a neat horizontal dash AT each category's x position
    # automatically — no manual coordinate arithmetic needed.
    # ─────────────────────────────────────────────────────────────────
    st.subheader("Stock Level vs Capacity")

    mat_shorts   = [item["material"].split("(")[0].strip()[:22] for item in inventory]
    pct_vals     = [(item["on_hand"] / item["max_capacity"]) * 100 for item in inventory]
    reorder_pcts = [(item["reorder_level"] / item["max_capacity"]) * 100 for item in inventory]
    bar_colors   = [
        "#ef4444" if item["on_hand"] <= item["reorder_level"] else
        "#f59e0b" if item["on_hand"] <= item["reorder_level"] * 1.4 else
        "#10b981"
        for item in inventory
    ]

    fig = go.Figure()

    # Single bar trace — all materials in one call
    fig.add_trace(go.Bar(
        x=mat_shorts,
        y=pct_vals,
        marker_color=bar_colors,
        text=[f"{p:.0f}%" for p in pct_vals],
        textposition="outside",
        showlegend=False,
        hovertemplate="%{x}<br>Stock: %{y:.1f}% of capacity<extra></extra>"
    ))

    # Threshold markers — scatter with "line-ew" symbol draws a
    # horizontal dash centred on each category automatically.
    fig.add_trace(go.Scatter(
        x=mat_shorts,
        y=reorder_pcts,
        mode="markers",
        marker=dict(
            symbol="line-ew",
            size=36,
            color="red",
            line=dict(color="red", width=2.5)
        ),
        name="Reorder threshold",
        hovertemplate="%{x}<br>Reorder at: %{y:.1f}%<extra></extra>"
    ))

    fig.update_layout(
        height=340,
        yaxis_title="% of Max Capacity",
        xaxis_title="",
        margin=dict(t=20, b=10),
        yaxis=dict(range=[0, 120]),
        legend=dict(orientation="h", yanchor="bottom", y=-0.25, xanchor="left", x=0)
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("🔴 Red dash markers = reorder threshold for each material")

    # Limiting material callout
    min_batches = min(
        int(item["on_hand"] / item.get(f"consumption_per_m3_{selected_grade}", 9999) / batch_size)
        if item.get(f"consumption_per_m3_{selected_grade}", 0) > 0 else 9999
        for item in inventory
    )
    limiting = next(
        item["material"] for item in inventory
        if item.get(f"consumption_per_m3_{selected_grade}", 0) > 0 and
           int(item["on_hand"] / item[f"consumption_per_m3_{selected_grade}"] / batch_size) == min_batches
    )
    st.info(f"**Limiting material for {selected_grade} at {batch_size}m³ batches: {limiting}** — only **{min_batches} batches** possible before stock-out.")


# ═════════════════════════════════════════════════════════════════════
# TAB 3 — ORDER INTAKE
# ═════════════════════════════════════════════════════════════════════
with tab_orders:
    st.title("📋 Order Intake")
    st.caption("Incoming order queue + AI-powered order parsing")
    st.divider()

    all_orders_display = orders + st.session_state.added_orders

    # ── AI Order Parser ────────────────────────────────────────────
    st.subheader("🤖 AI Order Parser")
    st.write(
        "Paste or type a raw order request — an email, a WhatsApp message, a phone note — "
        "and the AI will extract the structured fields automatically."
    )

    sample_texts = {
        "Select a sample...": "",
        "Email from site manager": "Hi, we need about 24 cubic metres of C40 precast beams for the Dundalk Industrial job. Can you deliver Thursday morning? Tight access so please send the smaller mixer. Thanks, Brendan",
        "WhatsApp voice-to-text": "yeah so we need like 16m3 of the hollow core slabs, grade C30 should be fine, for the Cavan apartments site, Friday afternoon ideally",
        "Formal PO text": "Please supply 32m³ C50 concrete (Box Culvert spec) to Dublin Port Tunnel site. Required delivery: tomorrow 07:00. Pump truck required.",
    }

    chosen = st.selectbox("Load a sample order text", list(sample_texts.keys()))
    raw_text = st.text_area("Order text", value=sample_texts[chosen], height=120,
                             placeholder="Type or paste the order request here...")

    parse_col, _ = st.columns([1, 3])
    _backend_ready = bool(groq_key)
    parse_btn = parse_col.button("🔍 Parse Order with AI", use_container_width=True,
                                  disabled=not groq_key or not raw_text.strip())

    if not groq_key:
        st.caption("⚠️ Add your Groq API key in the sidebar to enable AI parsing.")

    if parse_btn and groq_key and raw_text.strip():
        with st.spinner("Parsing order..."):
            try:
                # Build a date context that Python computes rather than the LLM.
                # LLMs are unreliable at calendar arithmetic — asking a model to figure
                # out "what date is next Thursday from a Friday?" produces wrong answers.
                # Instead we pre-compute every named day for the next 7 days and inject
                # the mapping as a lookup table. The model just reads "Thursday = 2026-05-28"
                # rather than calculating it, eliminating an entire class of date errors.
                _today = datetime.now()
                _upcoming = "\n".join(
                    f"  {(_today + timedelta(days=i)).strftime('%A')} = "
                    f"{(_today + timedelta(days=i)).strftime('%Y-%m-%d')}"
                    for i in range(1, 8)
                )
                _date_context = (
                    f"Today is {_today.strftime('%A, %d %B %Y')}.\n"
                    f"Today's date: {_today.strftime('%Y-%m-%d')}.\n"
                    f"Upcoming named days (use these exactly for day-name resolution):\n"
                    f"{_upcoming}"
                )
                parsed = parse_order(
                        groq_key,
                        raw_text,
                        _date_context,   # rich date context — no arithmetic needed from the model
                    )
                st.session_state["last_parsed"] = parsed
            except Exception as e:
                st.error(f"Parse error: {e}")

    if "last_parsed" in st.session_state:
        p = st.session_state["last_parsed"]
        st.success("✅ Order parsed — review and confirm below")

        with st.expander("Parsed Fields", expanded=True):
            c1, c2, c3 = st.columns(3)
            c1.markdown(f"**Customer:** {p.get('customer','—')}")
            c1.markdown(f"**Product:** {p.get('product_type','—')}")
            c1.markdown(f"**Grade:** {p.get('grade','—')}")
            c2.markdown(f"**Volume:** {p.get('volume_m3','—')} m³")
            c2.markdown(f"**Delivery:** {p.get('delivery_date','—')} {p.get('delivery_time','')}")
            c2.markdown(f"**County:** {p.get('county','—')}")
            c3.markdown(f"**Notes:** {p.get('notes','—') or '—'}")

        # Live fulfillability check on the parsed order
        grade_p  = p.get("grade","C40")
        volume_p = float(p.get("volume_m3") or 8.0)
        deliv_p  = p.get("delivery_date", datetime.now().strftime("%Y-%m-%d"))
        dtime_p  = p.get("delivery_time", None)
        fcheck   = check_fulfillability(grade_p, volume_p, inventory)
        pri_p, pri_reason_p = compute_priority(deliv_p, fcheck, delivery_time_str=dtime_p)

        if fcheck["can_fulfill"]:
            st.success(fcheck["summary"])
        else:
            st.error(fcheck["summary"])
            for s in fcheck["shortage_items"]:
                st.caption(
                    f"  • {s['material']}: need {s['needed']:.1f} {s['unit']}, "
                    f"have {s['on_hand']:.1f} — shortfall {s['shortfall']:.1f}. "
                    f"Supplier: {s['supplier']} | Lead time: {s['lead_time_days']}d"
                )
        if fcheck["low_stock_warnings"]:
            for w in fcheck["low_stock_warnings"]:
                st.warning(
                    f"⚠️ {w['material'].split('(')[0].strip()}: will drop to "
                    f"{w['remaining_after_this_order']:.1f} {w['unit']} after this order "
                    f"(reorder at {w['reorder_level']:.0f} {w['unit']}, lead time {w['lead_time_days']}d)"
                )
        st.info(f"**Computed priority:** {PRIORITY_DISPLAY.get(pri_p, pri_p)} — {pri_reason_p}")

        confirm_col, _ = st.columns([1, 3])
        if confirm_col.button("✅ Add to Order Queue", use_container_width=True):
            # Find matching site or create generic one
            matched_site = next(
                (s for s in sites if p.get("county","").lower() in s["county"].lower()), sites[0]
            )
            new_order = {
                "order_id": f"ORD-2025-{1200 + len(st.session_state.added_orders)}",
                "customer": p.get("customer", "Unknown"),
                "site_id": matched_site["id"],
                "site_lat": matched_site["lat"],
                "site_lon": matched_site["lon"],
                "county": p.get("county", matched_site["county"]),
                "product_type": p.get("product_type", "Hollow-Core Slab"),
                "grade": p.get("grade", "C40"),
                "volume_m3": float(p.get("volume_m3") or 8.0),
                "delivery_date": p.get("delivery_date", datetime.now().strftime("%Y-%m-%d")),
                "delivery_time": p.get("delivery_time", "08:00"),
                "status": "pending",
                "priority": "normal",
                "notes": p.get("notes", ""),
                "po_number": f"PO-AI-{9000 + len(st.session_state.added_orders)}"
            }
            st.session_state.added_orders.append(new_order)
            # Refresh dispatch
            all_o = orders + st.session_state.added_orders
            st.session_state.dispatch_result = run_dispatch(all_o, trucks)
            del st.session_state["last_parsed"]
            st.success(f"Order {new_order['order_id']} added!")
            st.rerun()

    st.divider()

    # ── Order Queue Table ──────────────────────────────────────────
    st.subheader(f"Order Queue ({len(all_orders_display)} orders)")

    priority_filter = st.multiselect(
        "Filter by priority",
        ["urgent", "high", "normal"],
        default=["urgent", "high", "normal"]
    )
    filtered = [o for o in all_orders_display if o.get("priority","normal") in priority_filter]

    status_color = {
        "pending": "🟡",
        "confirmed": "🔵",
        "in_production": "🟣",
    }
    priority_color = {
        "urgent": "🔴",
        "high": "🟠",
        "normal": "⚪"
    }

    today_str = datetime.now().strftime("%Y-%m-%d")

    rows = []
    for o in filtered:
        # Enrich with live fulfillability if not already stored
        if "fulfillability" not in o:
            o = enrich_order(o, inventory)

        deliv  = o.get("delivery_date","")
        try:
            days_delta = (datetime.strptime(deliv,"%Y-%m-%d") - datetime.now().replace(hour=0,minute=0,second=0,microsecond=0)).days
        except Exception:
            days_delta = 0

        if days_delta < 0:
            timeline_tag = f"⏰ Overdue ({abs(days_delta)}d ago)"
        elif days_delta == 0:
            timeline_tag = "🔴 Today"
        elif days_delta == 1:
            timeline_tag = "🟠 Tomorrow"
        else:
            timeline_tag = f"🟢 In {days_delta}d"

        fcheck = o.get("fulfillability") or check_fulfillability(o.get("grade","C40"), o.get("volume_m3",8.0), inventory)
        fulfill_tag = "✅ Yes" if fcheck["can_fulfill"] else f"❌ No — {fcheck['shortage_items'][0]['material'].split('(')[0].strip()} short" if fcheck["shortage_items"] else "❌ No"

        pri   = o.get("priority","normal")
        stat  = o.get("status","pending")
        rows.append({
            "Order ID":        o["order_id"],
            "Customer/Site":   o["customer"],
            "Product":         o["product_type"],
            "Grade":           o["grade"],
            "Vol (m³)":        o["volume_m3"],
            "County":          o["county"],
            "Delivery":        f"{deliv} {o.get('delivery_time','')}",
            "Timeline":        timeline_tag,
            "Fulfillable":     fulfill_tag,
            "Priority":        PRIORITY_DISPLAY.get(pri, pri),
            "Priority Reason": o.get("priority_reason",""),
            "Status":          f"{STATUS_EMOJI.get(stat,'📋')} {STATUS_DISPLAY.get(stat, stat)}",
            "Notes":           o.get("notes",""),
        })

    df_orders = pd.DataFrame(rows)

    # Explanation of priority logic — the head asked for this to be visible
    with st.expander("ℹ️ How priority is calculated", expanded=False):
        st.markdown("""
**Priority is a composite of delivery urgency AND material availability:**

| Level | Condition |
|-------|-----------|
| 🚨 Critical | Same-day or next-day delivery AND materials insufficient — needs immediate management action |
| 🔴 Urgent | Same-day delivery AND materials available — dispatch now |
| 🟠 High | Next-day delivery, OR materials insufficient for a future order |
| 👁 Watch | Materials available but stock will drop near reorder level after this order |
| ⚪ Normal | Future delivery AND all materials available |

*Overdue orders (delivery date passed) are automatically flagged High regardless of material status.*
        """)

    st.dataframe(df_orders, use_container_width=True, hide_index=True,
        column_config={
            "Priority Reason": st.column_config.TextColumn("Priority Reason", width="large"),
        }
    )

    # Download button
    csv_data = df_orders.to_csv(index=False)
    st.download_button(
        label="⬇️ Download Order Queue (CSV)",
        data=csv_data,
        file_name=f"precast_order_queue_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
        use_container_width=False
    )


# ═════════════════════════════════════════════════════════════════════
# TAB 4 — SMART DISPATCH
# ═════════════════════════════════════════════════════════════════════
with tab_dispatch:
    st.title("🚛 Smart Dispatch Scheduler")
    st.caption("AI-optimised batch scheduling, truck allocation & route sequencing — minimising idle time & slump risk")
    st.divider()

    dispatch = st.session_state.dispatch_result
    assigned   = [d for d in dispatch if d.get("assigned")]
    unassigned = [d for d in dispatch if not d.get("assigned")]

    # Orders already in production are intentionally excluded from scheduling —
    # their concrete is already being mixed or cast, so dispatch has nothing to decide.
    all_orders_pool = orders + st.session_state.added_orders
    in_production_count = sum(1 for o in all_orders_pool if o.get("status") == "in_production")
    total_order_count   = len(all_orders_pool)

    # Summary KPIs
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Assignments Today",   len(assigned))
    k2.metric("Unassigned Orders",   len(unassigned))
    total_vol = sum(d["volume_m3"] for d in assigned)
    k3.metric("Total Volume",        f"{total_vol:.0f} m³")
    high_risk = sum(1 for d in assigned if d.get("slump_risk") in ("HIGH","MEDIUM"))
    k4.metric("Slump Risk Alerts",   high_risk, delta_color="inverse", delta=None)

    # Surface the in_production exclusion so the count difference is transparent.
    # Without this caption, a viewer comparing the Order Queue (13 orders) to
    # Assignments Today (11) will wonder where 2 orders went.
    if in_production_count:
        st.caption(
            f"ℹ️  {in_production_count} of {total_order_count} orders are already **in production** "
            f"(concrete is being mixed or cast) and are excluded from dispatch scheduling. "
            f"Scheduling applies to pending and confirmed orders only."
        )

    if high_risk:
        st.warning(f"⚠️  **{high_risk} delivery(s)** have elevated slump risk due to distance/volume. Review the schedule below.")

    st.divider()

    # ── Dispatch table ─────────────────────────────────────────────
    st.subheader("Dispatch Schedule")

    risk_color = {"NONE": "🟢", "LOW": "🟡", "MEDIUM": "🟠", "HIGH": "🔴"}

    df_disp = pd.DataFrame([{
        "Order":          d["order_id"],
        "Site":           d["customer"],
        "County":         d["county"],
        "Grade":          d["grade"],
        "Volume (m³)":    d["volume_m3"],
        "Truck":          d.get("truck_id","—"),
        "Reg":            d.get("truck_reg","—"),
        "Driver":         d.get("driver","—"),
        "Dispatch":       d.get("dispatch_time","—"),
        "Arrival":        d.get("arrival_time","—"),
        "Dist (km)":      d.get("distance_km","—"),
        "Travel (min)":   d.get("travel_mins","—"),
        "Buffer (min)":   d.get("buffer_mins","—"),
        "Slump Risk":     f"{risk_color.get(d.get('slump_risk','NONE'),'⚪')} {d.get('slump_risk','—')}",
        "Fuel (€)":       d.get("fuel_cost_eur","—"),
    } for d in assigned])

    st.dataframe(df_disp, use_container_width=True, hide_index=True)

    if unassigned:
        st.error(f"**{len(unassigned)} orders could not be assigned** — insufficient trucks or capacity.")
        for u in unassigned:
            st.caption(f"❌ {u['order_id']} — {u['customer']} ({u['volume_m3']} m³): {u.get('reason','unknown')}")

    st.divider()

    # ── Gantt chart ────────────────────────────────────────────────
    st.subheader("Truck Timeline (Gantt)")

    gantt_rows = []
    for d in assigned:
        if "dispatch_dt" in d and "return_dt" in d:
            gantt_rows.append({
                "Truck": d["truck_id"],
                "Order": d["order_id"],
                "Site":  d["customer"],
                "Start": d["dispatch_dt"],
                "End":   d["return_dt"],
                "Slump Risk": d.get("slump_risk","NONE"),
                "Grade": d["grade"]
            })

    if gantt_rows:
        df_gantt = pd.DataFrame(gantt_rows)
        df_gantt["Start"] = pd.to_datetime(df_gantt["Start"])
        df_gantt["End"]   = pd.to_datetime(df_gantt["End"])

        color_map = {"NONE": "#10b981", "LOW": "#f59e0b", "MEDIUM": "#f97316", "HIGH": "#ef4444"}
        fig_gantt = px.timeline(
            df_gantt,
            x_start="Start", x_end="End",
            y="Truck",
            color="Slump Risk",
            color_discrete_map=color_map,
            hover_data=["Order", "Site", "Grade"],
            labels={"Slump Risk": "Slump Risk Level"}
        )
        fig_gantt.update_yaxes(autorange="reversed")
        fig_gantt.update_layout(height=300, margin=dict(t=10, b=10))
        st.plotly_chart(fig_gantt, use_container_width=True)
        st.caption("Green = safe  |  Amber = marginal  |  Orange = medium risk  |  Red = HIGH slump risk")

    st.divider()

    # ── Route Map (ORS-enabled) ───────────────────────────────────────
    st.subheader("🗺️ Dispatch Route Map")

    # If an ORS API key is set (from sidebar or .env), fetch real Irish road
    # polylines.  We cache the geometry in session state keyed by the set of
    # order IDs so we only call ORS when assignments change, not on every
    # Streamlit re-run.
    ors_api_key = os.environ.get("ORS_API_KEY", "").strip()

    if ors_api_key:
        cache_key = "ors_" + "_".join(d["order_id"] for d in assigned)
        if st.session_state.get("ors_cache_key") != cache_key:
            with st.spinner("Fetching real road routes from OpenRouteService..."):
                geometries = fetch_all_dispatch_routes(ors_api_key, assigned)
            st.session_state["ors_geometries"] = geometries
            st.session_state["ors_cache_key"]  = cache_key
        else:
            geometries = st.session_state.get("ors_geometries", {})
        using_ors = True
    else:
        geometries = {}
        using_ors = False

    # One hex colour per truck — preserving insertion order so the same
    # truck always gets the same colour within a session.
    TRUCK_HEX = ["#3b82f6","#10b981","#8b5cf6","#f59e0b","#ef4444","#06b6d4"]
    truck_ids = list(dict.fromkeys(d["truck_id"] for d in assigned if d.get("assigned")))

    m2 = folium.Map(location=[53.9069, -6.8092], zoom_start=8, tiles="CartoDB positron")
    folium.Marker(
        [53.9069, -6.8092],
        popup="🏭 Apex Precast Plant — Athlone, Co. Westmeath",
        icon=folium.Icon(color="red", icon="industry", prefix="fa"),
        tooltip="🏭 Plant"
    ).add_to(m2)

    risk_icons = {"NONE": "✅", "LOW": "🟡", "MEDIUM": "🟠", "HIGH": "🔴"}

    for d in assigned:
        if not d.get("assigned"):
            continue
        tid      = d["truck_id"]
        color    = TRUCK_HEX[truck_ids.index(tid) % len(TRUCK_HEX)]
        site_lat = d["site_lat"]
        site_lon = d["site_lon"]

        # Road geometry: ORS polyline if available, straight line as fallback.
        # Straight-line fallback ensures the map never breaks if ORS is down
        # or a key isn't provided — critical for live demos.
        road_coords = geometries.get(d["order_id"])
        folium.PolyLine(
            road_coords if road_coords else [[53.9069, -6.8092], [site_lat, site_lon]],
            color=color,
            weight=3.5 if road_coords else 2.0,
            opacity=0.8 if road_coords else 0.5,
            tooltip=f"{tid} ({d.get('driver','?')}) → {d['customer']}"
        ).add_to(m2)

        folium.CircleMarker(
            [site_lat, site_lon],
            radius=8,
            color=color, fill=True, fill_opacity=0.85,
            popup=folium.Popup(
                f"<b>{d['customer']}</b><br>"
                f"Order: {d['order_id']}<br>"
                f"Grade: {d['grade']} | {d['volume_m3']} m³<br>"
                f"Truck: {tid} — {d.get('driver','?')}<br>"
                f"Dispatch: {d.get('dispatch_time','?')} | Arrival: {d.get('arrival_time','?')}<br>"
                f"Distance: {d.get('distance_km','?')} km<br>"
                f"Slump Risk: {risk_icons.get(d.get('slump_risk','NONE'),'⚪')} {d.get('slump_risk','?')}<br>"
                f"Buffer: {d.get('buffer_mins','?')} min",
                max_width=260
            ),
            tooltip=f"{d['order_id']} — {d['customer']} ({d.get('slump_risk','?')} risk)"
        ).add_to(m2)

    st_folium(m2, height=440, use_container_width=True)
    route_label = (
        "🛣️ Routes follow real Irish roads via OpenRouteService (HGV profile)"
        if using_ors else
        "📏 Straight-line routes shown — add ORS API key in sidebar for real road geometry"
    )
    st.caption(f"Each colour = one truck · Click markers for delivery details · {route_label}")



# ═════════════════════════════════════════════════════════════════════
# TAB 5 — TRACEABILITY
# ═════════════════════════════════════════════════════════════════════
with tab_trace:
    st.title("🔍 Batch Traceability")
    st.caption("RFID/QR linkage from mix batch → casting → yard storage → site installation · Full audit trail for QA")
    st.divider()

    # Lookup
    batch_ids = [b["batch_id"] for b in batches]
    lookup_col, search_col = st.columns([2, 1])
    selected_batch_id = lookup_col.selectbox("Select Batch ID", batch_ids)
    search_col.markdown("<br>", unsafe_allow_html=True)

    # Find the batch
    batch = next((b for b in batches if b["batch_id"] == selected_batch_id), None)

    if batch:
        # ── Order association ──────────────────────────────────────────────
        # Find the parent order for this batch
        associated_order = next(
            (o for o in (orders + st.session_state.added_orders)
             if o["order_id"] == batch.get("order_id")), None
        )
        # Find all other batches that belong to the same order (1-to-N)
        sibling_batches = [
            b for b in batches
            if b["order_id"] == batch.get("order_id") and b["batch_id"] != batch["batch_id"]
        ]

        # Show order linkage prominently — the head asked for accountability
        # on the order → batch relationship
        if associated_order:
            st.markdown(
                f"**Associated Order:** `{batch['order_id']}`  ·  "
                f"Customer: **{associated_order['customer']}**  ·  "
                f"Grade: **{associated_order['grade']}**  ·  "
                f"Total order volume: **{associated_order['volume_m3']} m³**  ·  "
                f"Status: **{STATUS_DISPLAY.get(associated_order['status'], associated_order['status'])}**"
            )
            if sibling_batches:
                sib_ids = ", ".join(f"`{b['batch_id']}`" for b in sibling_batches)
                st.markdown(
                    f"**Other batches for this order ({len(sibling_batches)}):** {sib_ids}  "
                    f"— this order required {len(sibling_batches)+1} mixer loads in total."
                )
        else:
            st.markdown(f"**Associated Order:** `{batch.get('order_id','—')}` (order record not found in current session)")

        st.divider()

        # Header info
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Grade",      batch["grade"])
        c2.metric("Volume",     f"{batch['volume_m3']} m³")
        c3.metric("QA Result",  batch["qa"]["overall_qa"],
                  delta_color="off" if batch["qa"]["overall_qa"]=="PASS" else "inverse")
        c4.metric("28d Strength", f"{batch['qa']['28day_strength_mpa']} MPa")

        st.markdown(f"**Truck:** {batch['truck_id']} ({batch['truck_reg']}) · **Driver:** {batch['driver']}")
        st.markdown(f"**Site:** {batch['site_name']} · **QR:** `{batch['qr_code']}` · **RFID:** `{batch['rfid_tag']}`")
        st.divider()

        # ── Stage Timeline ─────────────────────────────────────────
        st.subheader("Journey Timeline")

        stage_names = {
            "mix":      ("⚙️ Mixing",          "#3b82f6"),
            "pour":     ("🏗️ Pouring / Mould", "#8b5cf6"),
            "cure":     ("🌡️ Curing",          "#f59e0b"),
            "yard":     ("🏚️ Yard Storage",    "#10b981"),
            "dispatch": ("🚛 Dispatch",         "#f97316"),
            "delivery": ("📍 Site Delivery",    "#ef4444"),
        }

        for stage_key, (stage_label, color) in stage_names.items():
            stage = batch["stages"].get(stage_key, {})
            if not stage:
                continue

            start_dt = datetime.fromisoformat(stage["start"])
            end_dt   = datetime.fromisoformat(stage["end"])
            duration_mins = int((end_dt - start_dt).total_seconds() / 60)

            col_icon, col_info = st.columns([1, 6])
            with col_icon:
                st.markdown(
                    f"<div style='background:{color};border-radius:50%;width:40px;height:40px;"
                    f"display:flex;align-items:center;justify-content:center;font-size:1.2rem'>"
                    f"{stage_label.split()[0]}</div>",
                    unsafe_allow_html=True
                )
            with col_info:
                st.markdown(f"**{stage_label}**")
                st.markdown(
                    f"`{start_dt.strftime('%d %b %Y, %H:%M')}` → `{end_dt.strftime('%H:%M')}`"
                    f"  ·  **{duration_mins} min**  ·  Operator: {stage.get('operator','?')}"
                )
            st.markdown("↓" if stage_key != "delivery" else "")

        st.divider()

        # ── QA Panel ──────────────────────────────────────────────
        st.subheader("QA Report")
        qa = batch["qa"]
        qc1, qc2, qc3, qc4 = st.columns(4)
        qc1.metric("Slump (mm)",     qa["slump_mm"],
                   delta="PASS" if qa["slump_pass"] else "FAIL",
                   delta_color="normal" if qa["slump_pass"] else "inverse")
        qc2.metric("W/C Ratio",      qa["water_cement_ratio"],
                   help="Lower is stronger. Target < 0.50 for C40+")
        qc3.metric("Air Content %",  qa["air_content_pct"],
                   help="Target 1.5–4%")
        qc4.metric("28d Strength",   f"{qa['28day_strength_mpa']} MPa",
                   delta="PASS" if qa["strength_pass"] else "FAIL",
                   delta_color="normal" if qa["strength_pass"] else "inverse")

        overall = qa["overall_qa"]
        if overall == "PASS":
            st.success(f"✅  Batch {selected_batch_id} — **QA PASSED**. All acceptance criteria met.")
        else:
            st.error(f"❌  Batch {selected_batch_id} — **QA FAILED**. Non-conformance report required.")


# ═════════════════════════════════════════════════════════════════════
# TAB 6 — AI COPILOT
# ═════════════════════════════════════════════════════════════════════
with tab_copilot:
    st.title("💬 Plant Intelligence Assistant")
    st.caption("Ask anything about your plant — inventory, dispatch, QA, risk, production status.")
    st.divider()

    if not groq_key:
        st.warning("⚠️  Add your Groq API key in the sidebar to use the AI Copilot.")
    else:
        # Suggested questions
        st.markdown("**Suggested questions:**")
        suggestions = [
            "Which batches are at slump risk today?",
            "How many C40 batches can we still produce today given current stock?",
            "Are there any QA failures in the last week and what grade were they?",
            "Which truck has the most deliveries today?",
            "What materials need to be reordered urgently?",
        ]
        sq_cols = st.columns(3)
        for i, s in enumerate(suggestions):
            if sq_cols[i % 3].button(s, use_container_width=True):
                st.session_state["copilot_question"] = s

        st.divider()

        question = st.text_area(
            "Your question",
            value=st.session_state.get("copilot_question",""),
            height=80,
            placeholder="e.g. Which orders are at risk of missing their delivery window today?"
        )

        if st.button("Ask AI", use_container_width=False, disabled=not question.strip()):
            with st.spinner("Thinking..."):
                # Build a concise context (avoid sending 20KB of JSON).
                # IMPORTANT: include consumption_per_m3 rates so the LLM can
                # calculate batch counts without hedging about "unknown mix design".
                context = {
                    # Inventory — includes consumption rates so LLM can answer mix questions
                    "inventory_summary": [
                        {"mat": i["material"], "on_hand": i["on_hand"], "unit": i["unit"],
                         "reorder": i["reorder_level"],
                         "status": "LOW" if i["on_hand"] <= i["reorder_level"]*1.4 else "OK",
                         "rate_C30": i.get("consumption_per_m3_C30"),
                         "rate_C40": i.get("consumption_per_m3_C40"),
                         "rate_C50": i.get("consumption_per_m3_C50"),
                         "lead_days": i.get("lead_time_days")}
                        for i in inventory
                    ],
                    "batch_size_m3": 8.0,

                    # PRE-COMPUTED BATCH CAPACITY (slim version — no per-material breakdown to save tokens)
                    # Full breakdown is available in inventory_summary for follow-up questions.
                    "batch_capacity_by_grade": {
                        grade: (lambda rows: {
                            "batches_possible": min(r["b"] for r in rows),
                            "limiting_material": min(rows, key=lambda r: r["b"])["m"],
                        })([
                            {"m": item["material"],
                             "b": int(item["on_hand"] / (item.get(f"consumption_per_m3_{grade}", 9999) * 8.0))
                                  if item.get(f"consumption_per_m3_{grade}", 0) > 0 else 9999}
                            for item in inventory if item.get(f"consumption_per_m3_{grade}", 0) > 0
                        ])
                        for grade in ["C30", "C40", "C50"]
                    },
                    "orders_today": [
                        {k: v for k, v in o.items() if k in
                         ("order_id","customer","grade","volume_m3","status","priority","delivery_date","county")}
                        for o in (orders + st.session_state.added_orders)
                    ],
                    # Slim dispatch context — only fields the LLM reasons about
                    "dispatch_assignments": [
                        {"order": d["order_id"], "truck": d.get("truck_id"),
                         "driver": d.get("driver"), "slump_risk": d.get("slump_risk"),
                         "arrival": d.get("arrival_time"), "dist_km": d.get("distance_km"),
                         "grade": d.get("grade"), "vol_m3": d.get("volume_m3")}
                        for d in st.session_state.dispatch_result if d.get("assigned")
                    ],
                    "qa_summary": {
                        "total_batches_last_14d": len(batches),
                        "pass_count": sum(1 for b in batches if b["qa"]["overall_qa"]=="PASS"),
                        "fail_count": sum(1 for b in batches if b["qa"]["overall_qa"]=="FAIL"),
                        # Full QA readings per failed batch — the LLM needs the actual
                        # measured values (slump_mm, wc_ratio, strength) to explain WHY
                        # a batch failed, not just that it did.  Without these numbers,
                        # all it can say is "failure reason unknown" even when the reason
                        # is clear from the data (e.g. slump=252mm for C30, limit=180mm).
                        "fail_details": [
                            {
                                "batch_id":          b["batch_id"],
                                "grade":             b["grade"],
                                "site":              b["site_name"],
                                "failure_reason":    b["qa"].get("failure_reason", "unknown"),
                                "slump_mm":          b["qa"]["slump_mm"],
                                "slump_pass":        b["qa"]["slump_pass"],
                                "slump_acceptance_range_mm": b["qa"].get("slump_acceptance_range_mm", "unknown"),
                                "water_cement_ratio": b["qa"]["water_cement_ratio"],
                                "28day_strength_mpa": b["qa"]["28day_strength_mpa"],
                                "strength_pass":     b["qa"]["strength_pass"],
                                "min_strength_required_mpa": b["qa"].get("min_strength_required_mpa", "unknown"),
                                "air_content_pct":   b["qa"]["air_content_pct"],
                                "overall_qa":        b["qa"]["overall_qa"],
                            }
                            for b in batches if b["qa"]["overall_qa"]=="FAIL"
                        ],
                        "grades_with_failures": list({b["grade"] for b in batches if b["qa"]["overall_qa"]=="FAIL"})
                    },
                    # Pre-compute delivery counts per truck in Python.
                    # NEVER ask the LLM to count items in a list — it frequently
                    # miscounts, especially for numbers > 3.  Give it the answer.
                    "slump_risk_summary": {
                            "high_risk": [d["order_id"] for d in st.session_state.dispatch_result
                                        if d.get("assigned") and d.get("slump_risk") == "HIGH"],
                            "medium_risk": [d["order_id"] for d in st.session_state.dispatch_result
                                            if d.get("assigned") and d.get("slump_risk") == "MEDIUM"],
                            "total_at_risk": sum(1 for d in st.session_state.dispatch_result
                                                if d.get("assigned") and d.get("slump_risk") in ("HIGH","MEDIUM"))
                        },
                    "truck_deliveries_today": {
                        tid: {
                            "driver": next((t["driver"] for t in trucks if t["id"]==tid), "?"),
                            "delivery_count": sum(1 for d in st.session_state.dispatch_result
                                                  if d.get("assigned") and d.get("truck_id")==tid),
                            "status": next((t["status"] for t in trucks if t["id"]==tid), "?"),
                            "capacity_m3": next((t["capacity_m3"] for t in trucks if t["id"]==tid), 0),
                        }
                        for tid in {d["truck_id"] for d in st.session_state.dispatch_result
                                    if d.get("assigned") and d.get("truck_id")}
                    },
                    "truck_with_most_deliveries": max(
                        ({"truck_id": tid,
                          "count": sum(1 for d in st.session_state.dispatch_result
                                       if d.get("assigned") and d.get("truck_id")==tid)}
                         for tid in {d["truck_id"] for d in st.session_state.dispatch_result
                                     if d.get("assigned") and d.get("truck_id")}),
                        key=lambda x: x["count"],
                        default={"truck_id": "none", "count": 0}
                    )
                }

                try:
                    answer = copilot_query(groq_key, question, context)
                    st.session_state["copilot_answer"] = answer
                    st.session_state["copilot_question_display"] = question
                    st.session_state.pop("copilot_question", None)
                except Exception as e:
                    st.error(f"Error: {e}")

        if "copilot_answer" in st.session_state:
            st.markdown(f"**Q: {st.session_state['copilot_question_display']}**")
            st.info(st.session_state["copilot_answer"])

            if st.button("Clear answer"):
                del st.session_state["copilot_answer"]
                del st.session_state["copilot_question_display"]
                st.rerun()
