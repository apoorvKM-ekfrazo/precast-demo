"""
data_generator.py
Generates all synthetic data for the Apex Precast AI Pilot.
Apex Precast is based in Athlone, Co. Westmeath, Ireland.
All data is realistic for an Irish precast concrete operation.
Run once: python data_generator.py   → writes JSON files to /data/
"""

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

random.seed(42)  # reproducible data

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

# ─────────────────────────────────────────────
# 1. PLANT & SITE LOCATIONS  (lat, lon)
# ─────────────────────────────────────────────
PLANT = {
    "name": "Apex Precast – Athlone Plant",
    "lat": 53.9069,
    "lon": -6.8092,
    "address": "Athlone, Co. Westmeath, A82 XK72"
}

SITES = [
    {"id": "SITE-01", "name": "Dublin Port Tunnel Extension", "lat": 53.3588, "lon": -6.2197, "county": "Dublin"},
    {"id": "SITE-02", "name": "Drogheda Road Bridge", "lat": 53.7176, "lon": -6.3561, "county": "Louth"},
    {"id": "SITE-03", "name": "Navan Retail Park", "lat": 53.6558, "lon": -6.6878, "county": "Meath"},
    {"id": "SITE-04", "name": "Dundalk Industrial Estate", "lat": 54.0027, "lon": -6.4173, "county": "Louth"},
    {"id": "SITE-05", "name": "Cavan Town Apartments", "lat": 53.9906, "lon": -7.3597, "county": "Cavan"},
    {"id": "SITE-06", "name": "Monaghan Bypass", "lat": 54.2492, "lon": -6.9682, "county": "Monaghan"},
    {"id": "SITE-07", "name": "Mullingar Data Centre", "lat": 53.5228, "lon": -7.3423, "county": "Westmeath"},
    {"id": "SITE-08", "name": "Athlone River Crossing", "lat": 53.4239, "lon": -7.9407, "county": "Westmeath"},
]

# ─────────────────────────────────────────────
# 2. INVENTORY  (raw materials)
# ─────────────────────────────────────────────
# Each grade (C30, C40, C50) consumes different kg of cement per m³.
# Average batch = 8m³ (full transit mixer)

INVENTORY = [
    {
        "material": "OPC 53 Grade Cement",
        "unit": "tonnes",
        "on_hand": 148.5,
        "reorder_level": 50.0,
        "max_capacity": 300.0,
        "consumption_per_m3_C30": 0.32,   # tonnes per m³
        "consumption_per_m3_C40": 0.38,
        "consumption_per_m3_C50": 0.44,
        "supplier": "Irish Cement Ltd, Platin",
        "lead_time_days": 2
    },
    {
        "material": "Coarse Aggregate 20mm",
        "unit": "tonnes",
        "on_hand": 210.0,
        "reorder_level": 80.0,
        "max_capacity": 500.0,
        "consumption_per_m3_C30": 1.10,
        "consumption_per_m3_C40": 1.05,
        "consumption_per_m3_C50": 1.00,
        "supplier": "Roadstone Ltd",
        "lead_time_days": 1
    },
    {
        "material": "Coarse Aggregate 10mm",
        "unit": "tonnes",
        "on_hand": 95.0,
        "reorder_level": 40.0,
        "max_capacity": 200.0,
        "consumption_per_m3_C30": 0.45,
        "consumption_per_m3_C40": 0.42,
        "consumption_per_m3_C50": 0.40,
        "supplier": "Roadstone Ltd",
        "lead_time_days": 1
    },
    {
        "material": "River Sand (Fine Aggregate)",
        "unit": "tonnes",
        "on_hand": 88.0,
        "reorder_level": 40.0,
        "max_capacity": 250.0,
        "consumption_per_m3_C30": 0.75,
        "consumption_per_m3_C40": 0.68,
        "consumption_per_m3_C50": 0.62,
        "supplier": "Local quarry, Bailieborough",
        "lead_time_days": 1
    },
    {
        "material": "Fly Ash (PFA)",
        "unit": "tonnes",
        "on_hand": 28.0,
        "reorder_level": 15.0,
        "max_capacity": 80.0,
        "consumption_per_m3_C30": 0.08,
        "consumption_per_m3_C40": 0.06,
        "consumption_per_m3_C50": 0.04,
        "supplier": "Moneypoint Power Station",
        "lead_time_days": 3
    },
    {
        "material": "Superplasticizer (SIKA ViscoCrete)",
        "unit": "litres",
        "on_hand": 1240.0,
        "reorder_level": 400.0,
        "max_capacity": 3000.0,
        "consumption_per_m3_C30": 2.5,
        "consumption_per_m3_C40": 3.5,
        "consumption_per_m3_C50": 5.0,
        "supplier": "SIKA Ireland",
        "lead_time_days": 2
    },
    {
        "material": "Steel Reinforcement (Rebar 12mm)",
        "unit": "tonnes",
        "on_hand": 42.0,
        "reorder_level": 10.0,
        "max_capacity": 100.0,
        "consumption_per_m3_C30": 0.12,
        "consumption_per_m3_C40": 0.14,
        "consumption_per_m3_C50": 0.16,
        "supplier": "Ennio Steel",
        "lead_time_days": 4
    },
]

# ─────────────────────────────────────────────
# 3. TRUCKS  (transit mixer fleet)
# ─────────────────────────────────────────────
TRUCKS = [
    {"id": "TRK-001", "reg": "CV-23-D-1142", "capacity_m3": 8.0,  "status": "available", "driver": "Padraig Murphy",   "lat": 53.9069, "lon": -6.8092, "last_service": "2025-04-10"},
    {"id": "TRK-002", "reg": "MH-22-MH-887", "capacity_m3": 8.0,  "status": "available", "driver": "Sean Fitzpatrick", "lat": 53.9069, "lon": -6.8092, "last_service": "2025-03-22"},
    {"id": "TRK-003", "reg": "LH-21-LH-445", "capacity_m3": 6.0,  "status": "en_route",  "driver": "Colm Brady",      "lat": 53.8200, "lon": -6.5100, "last_service": "2025-05-01"},
    {"id": "TRK-004", "reg": "CN-20-CN-334", "capacity_m3": 8.0,  "status": "available", "driver": "Kieran O'Brien",  "lat": 53.9069, "lon": -6.8092, "last_service": "2025-04-28"},
    {"id": "TRK-005", "reg": "MN-23-MN-291", "capacity_m3": 9.0,  "status": "maintenance","driver": "Declan Reilly",  "lat": 53.9069, "lon": -6.8092, "last_service": "2025-02-15"},
    {"id": "TRK-006", "reg": "WH-22-WH-603", "capacity_m3": 8.0,  "status": "available", "driver": "Michael Dunne",   "lat": 53.9069, "lon": -6.8092, "last_service": "2025-05-05"},
]

# ─────────────────────────────────────────────
# 4. ORDERS  (incoming precast orders)
# ─────────────────────────────────────────────
PRODUCT_TYPES = [
    "Hollow-Core Slab",
    "Precast Beam (I-Section)",
    "Box Culvert",
    "Precast Column",
    "Retaining Wall Panel",
    "Double-T Slab",
    "Manhole Ring",
    "Bridge Parapet"
]

def make_orders():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    orders = []
    for i in range(12):
        site = random.choice(SITES)
        grade = random.choice(["C30", "C30", "C40", "C40", "C50"])  # C40 most common
        volume = round(random.uniform(6, 48), 1)
        product = random.choice(PRODUCT_TYPES)
        delivery_offset = random.randint(0, 5)
        delivery_date = today + timedelta(days=delivery_offset)
        hour = random.choice([7, 8, 9, 10, 11, 13, 14])
        statuses = ["pending", "pending", "pending", "confirmed", "confirmed", "in_production"]
        status = statuses[i % len(statuses)]
        priority = "urgent" if delivery_offset == 0 else ("high" if delivery_offset == 1 else "normal")

        orders.append({
            "order_id": f"ORD-2025-{1100 + i}",
            "customer": site["name"],
            "site_id": site["id"],
            "site_lat": site["lat"],
            "site_lon": site["lon"],
            "county": site["county"],
            "product_type": product,
            "grade": grade,
            "volume_m3": volume,
            "delivery_date": delivery_date.strftime("%Y-%m-%d"),
            "delivery_time": f"{hour:02d}:00",
            "status": status,
            "priority": priority,
            "notes": random.choice([
                "Pump required on site",
                "Tight access — small mixer preferred",
                "",
                "Client on site — call 30 min before",
                ""
            ]),
            "po_number": f"PO-ORP-{4000 + i}"
        })
    return orders

# ─────────────────────────────────────────────
# 5. BATCH HISTORY  (for traceability)
# ─────────────────────────────────────────────
def make_batches():
    batches = []
    for i in range(20):
        batch_date = datetime.now() - timedelta(days=random.randint(0, 14))
        grade = random.choice(["C30", "C40", "C40", "C50"])
        volume = round(random.choice([6.0, 7.5, 8.0]), 1)
        site = random.choice(SITES)
        truck = random.choice([t for t in TRUCKS if t["status"] != "maintenance"])

        # Build stage timestamps sequentially
        t0 = batch_date.replace(hour=6, minute=random.randint(0, 30))
        mix_start    = t0
        mix_end      = t0 + timedelta(minutes=random.randint(8, 15))
        pour_start   = mix_end + timedelta(minutes=random.randint(5, 20))
        pour_end     = pour_start + timedelta(minutes=random.randint(20, 45))
        cure_start   = pour_end
        cure_end     = cure_start + timedelta(hours=random.randint(18, 28))
        yard_start   = cure_end
        yard_end     = yard_start + timedelta(hours=random.randint(4, 48))
        dispatch_ts  = yard_end
        delivery_ts  = dispatch_ts + timedelta(hours=random.uniform(0.5, 2.5))

        # ── QA DATA — measured values must be CONSISTENT with pass/fail ────────
        # A common synthetic data mistake is to randomly set slump_pass=False
        # while leaving slump_mm=150 (a perfectly good reading), which any plant
        # manager would immediately call out.  Instead we decide the outcome first,
        # then generate a measurement that actually justifies that outcome.
        #
        # Slump acceptance ranges (EN 206 / Irish standards):
        #   C30: target 80–160mm, reject if < 60mm or > 190mm
        #   C40: target 100–180mm, reject if < 70mm or > 210mm
        #   C50: high workability with superplasticizer, target 170–210mm,
        #        reject if < 130mm (stiff/segregated) or > 230mm (too wet)
        #
        # Strength acceptance (characteristic compressive strength at 28 days):
        #   C30: must exceed 30 MPa
        #   C40: must exceed 40 MPa
        #   C50: must exceed 50 MPa

        SLUMP_OK_RANGE = {
            "C30": (80,  160),
            "C40": (100, 180),
            "C50": (170, 210),
        }
        STRENGTH_OK_MIN  = {"C30": 30.0, "C40": 40.0, "C50": 50.0}
        STRENGTH_OK_MAX  = {"C30": 42.0, "C40": 54.0, "C50": 67.0}
        STRENGTH_FAIL_RANGE = {
            "C30": (20.0, 29.5),
            "C40": (30.0, 39.5),
            "C50": (40.0, 49.5),
        }

        slump_ok    = random.random() > 0.08   # 92% pass — decide outcome first
        strength_ok = random.random() > 0.04   # 96% pass

        if slump_ok:
            lo, hi = SLUMP_OK_RANGE[grade]
            slump_mm = random.randint(lo, hi)  # realistic passing reading
        else:
            # Failure: too stiff (under-watered) OR too wet (over-watered/segregated)
            if random.random() < 0.6:
                slump_mm = random.randint(15, 55)   # too stiff — common in cold weather
            else:
                slump_mm = random.randint(220, 265) # too wet — admixture overdose

        if strength_ok:
            lo = STRENGTH_OK_MIN[grade]
            hi = STRENGTH_OK_MAX[grade]
            strength_mpa = round(random.uniform(lo, hi), 1)
        else:
            lo, hi = STRENGTH_FAIL_RANGE[grade]
            strength_mpa = round(random.uniform(lo, hi), 1)

        # W/C ratio: lower ratio → higher strength.  Fail batches tend to have
        # higher W/C (too much water added on-site, a common real-world problem).
        wc_ratio = round(
            random.uniform(0.38, 0.46) if strength_ok
            else random.uniform(0.52, 0.62),   # over-watered batch
            2
        )

        batches.append({
            "batch_id": f"BCH-2025-{2000 + i}",
            "order_id": f"ORD-2025-{1100 + (i % 12)}",
            "grade": grade,
            "volume_m3": volume,
            "truck_id": truck["id"],
            "truck_reg": truck["reg"],
            "driver": truck["driver"],
            "site_id": site["id"],
            "site_name": site["name"],
            "qr_code": f"ORP-QR-{3000 + i}",
            "rfid_tag": f"RFID-{hex(random.randint(0xA000, 0xFFFF))[2:].upper()}",
            "stages": {
                "mix":      {"start": mix_start.isoformat(),   "end": mix_end.isoformat(),    "status": "complete", "operator": "Plant Control"},
                "pour":     {"start": pour_start.isoformat(),  "end": pour_end.isoformat(),   "status": "complete", "operator": "Mould Team B"},
                "cure":     {"start": cure_start.isoformat(),  "end": cure_end.isoformat(),   "status": "complete", "operator": "Curing Bay 2"},
                "yard":     {"start": yard_start.isoformat(),  "end": yard_end.isoformat(),   "status": "complete", "operator": "Yard Team"},
                "dispatch": {"start": dispatch_ts.isoformat(), "end": dispatch_ts.isoformat(),"status": "complete", "operator": truck["driver"]},
                "delivery": {"start": delivery_ts.isoformat(), "end": delivery_ts.isoformat(),"status": "complete", "operator": truck["driver"]},
            },
            "qa": {
                "slump_mm": slump_mm,
                "slump_pass": slump_ok,
                "slump_acceptance_range_mm": list(SLUMP_OK_RANGE[grade]),
                "water_cement_ratio": wc_ratio,
                "air_content_pct": round(random.uniform(1.5, 4.5), 1),
                "28day_strength_mpa": strength_mpa,
                "strength_pass": strength_ok,
                "min_strength_required_mpa": STRENGTH_OK_MIN[grade],
                "overall_qa": "PASS" if (slump_ok and strength_ok) else "FAIL",
                "failure_reason": (
                    None if (slump_ok and strength_ok)
                    else ("Low slump" if (not slump_ok and slump_mm < 100) else
                          "High slump (segregation risk)" if (not slump_ok) else
                          f"Low 28-day strength ({strength_mpa} MPa < {STRENGTH_OK_MIN[grade]} MPa required)")
                )
            }
        })
    return batches

# ─────────────────────────────────────────────
# 6. WRITE ALL DATA FILES
# ─────────────────────────────────────────────
if __name__ == "__main__":
    orders  = make_orders()
    batches = make_batches()

    with open(DATA_DIR / "plant.json",     "w") as f: json.dump(PLANT,     f, indent=2)
    with open(DATA_DIR / "sites.json",     "w") as f: json.dump(SITES,     f, indent=2)
    with open(DATA_DIR / "inventory.json", "w") as f: json.dump(INVENTORY, f, indent=2)
    with open(DATA_DIR / "trucks.json",    "w") as f: json.dump(TRUCKS,    f, indent=2)
    with open(DATA_DIR / "orders.json",    "w") as f: json.dump(orders,    f, indent=2)
    with open(DATA_DIR / "batches.json",   "w") as f: json.dump(batches,   f, indent=2)

    print(f"✅ Data generated:")
    print(f"   {len(INVENTORY)} inventory items")
    print(f"   {len(TRUCKS)} trucks")
    print(f"   {len(orders)} orders")
    print(f"   {len(batches)} batch records")
    print(f"   {len(SITES)} delivery sites")
