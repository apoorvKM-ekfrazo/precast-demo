"""
utils/scheduler.py
Greedy dispatch scheduler for transit mixer trucks.

The core constraint in precast concrete logistics that makes this non-trivial:
concrete begins to lose workability (slump) after ~90 minutes from mixing.
So a truck can't be scheduled on a second delivery if the total cycle time
(load + travel + pour + return) would leave no buffer before slump onset.

We model this as a simple greedy earliest-available-truck algorithm with
a slump-risk flag when the delivery window is tight.
"""

import math
from datetime import datetime, timedelta
from typing import List, Dict, Any


# ─────────────────────────────────────────────
# Distance helper (Haversine formula)
# ─────────────────────────────────────────────
def haversine_km(lat1, lon1, lat2, lon2) -> float:
    """
    Returns road-approximate distance in km between two lat/lon points.
    We multiply the straight-line distance by 1.35 to approximate Irish
    road geometry (winding roads, towns, etc.).
    """
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    straight_km = 2 * R * math.asin(math.sqrt(a))
    return round(straight_km * 1.35, 1)  # road-factor correction


def travel_time_mins(distance_km: float, speed_kmh: float = 65.0) -> int:
    """
    Loaded transit mixers average ~65 km/h on Irish national roads.
    Returns travel time in minutes, minimum 10.
    """
    return max(10, int((distance_km / speed_kmh) * 60))


# ─────────────────────────────────────────────
# Slump risk assessment
# ─────────────────────────────────────────────
SLUMP_LIMIT_MINS   = 90   # concrete unusable after 90 min from batching
SLUMP_WARN_MINS    = 70   # warn if delivery + pour exceeds this
POUR_TIME_PER_M3   = 4    # minutes per m³ of concrete placed on site

def assess_slump_risk(distance_km: float, volume_m3: float) -> Dict:
    """
    Calculates total cycle time and flags slump risk.
    
    The clock starts when the drum starts turning at the plant.
    We account for:
      - Loading time (fixed 10 min)
      - Travel to site
      - Pour time (proportional to volume)
    If total > SLUMP_WARN_MINS the batch is flagged as at risk.
    """
    load_mins   = 10
    travel_mins = travel_time_mins(distance_km)
    pour_mins   = int(volume_m3 * POUR_TIME_PER_M3)
    total_mins  = load_mins + travel_mins + pour_mins
    
    buffer_mins = SLUMP_LIMIT_MINS - total_mins
    
    if buffer_mins < 0:
        risk = "HIGH"      # already over limit — should not dispatch
    elif buffer_mins < 15:
        risk = "MEDIUM"    # very tight
    elif total_mins > SLUMP_WARN_MINS:
        risk = "LOW"       # margeable but notable
    else:
        risk = "NONE"
    
    return {
        "load_mins": load_mins,
        "travel_mins": travel_mins,
        "pour_mins": pour_mins,
        "total_mins": total_mins,
        "buffer_mins": buffer_mins,
        "slump_risk": risk
    }


# ─────────────────────────────────────────────
# Main scheduler
# ─────────────────────────────────────────────
PLANT_LAT = 53.9069
PLANT_LON = -6.8092
PLANT_RETURN_SPEED = 80.0  # faster return (empty drum)


def run_dispatch(orders: List[Dict], trucks: List[Dict]) -> List[Dict]:
    """
    Greedy earliest-available scheduler.
    
    For each order (sorted by delivery datetime ascending), assign the
    truck that becomes free earliest AND can complete the delivery within
    the slump window.

    Returns a list of dispatch assignments enriched with timing details,
    slump risk, and the Gantt bar data for visualisation.
    """
    # Only schedule confirmed/pending orders with a delivery_date
    schedulable = [
        o for o in orders
        if o["status"] in ("pending", "confirmed")
    ]
    schedulable.sort(key=lambda o: (o["delivery_date"], o["delivery_time"]))

    # Initialise truck availability — each truck becomes free at "now"
    now = datetime.now().replace(second=0, microsecond=0)
    available_trucks = {
        t["id"]: {
            **t,
            "free_at": now if t["status"] == "available" else now + timedelta(hours=2),
        }
        for t in trucks
        if t["status"] != "maintenance"
    }

    assignments = []

    for order in schedulable:
        site_lat = order["site_lat"]
        site_lon = order["site_lon"]
        volume   = order["volume_m3"]
        grade    = order["grade"]

        dist_km = haversine_km(PLANT_LAT, PLANT_LON, site_lat, site_lon)
        # Slump risk is assessed per truckload (max 8m³), not full order volume.
        # Each load has its own 90-minute clock from the moment it starts mixing.
        load_volume = min(volume, 8.0)
        slump = assess_slump_risk(dist_km, load_volume)

        # How many mixer-loads needed? (each truck does max 8m³)
        loads_needed = math.ceil(volume / 8.0)

        # For simplicity assign the best available truck for load 1
        # A real system would handle multi-truck coordination
        best_truck = None
        best_free  = None

        for tid, truck in available_trucks.items():
            # Check capacity — prefer 8m³ trucks for large orders
            if truck["capacity_m3"] < min(volume, 8.0):
                continue
            if best_free is None or truck["free_at"] < best_free:
                best_truck = truck
                best_free  = truck["free_at"]

        if best_truck is None:
            assignments.append({
                **order,
                "assigned": False,
                "reason": "No available truck within capacity constraints",
                "distance_km": dist_km,
                **slump
            })
            continue

        # Calculate timestamps
        dispatch_time  = best_truck["free_at"]
        depart_time    = dispatch_time + timedelta(minutes=10)   # load time
        arrival_time   = depart_time   + timedelta(minutes=slump["travel_mins"])
        pour_end_time  = arrival_time  + timedelta(minutes=slump["pour_mins"])
        return_time    = pour_end_time + timedelta(
            minutes=travel_time_mins(dist_km, speed_kmh=PLANT_RETURN_SPEED)
        )

        # Update truck's next available time
        available_trucks[best_truck["id"]]["free_at"] = return_time

        # Cost estimate (Irish diesel ~€1.80/L, mixer does ~4 km/L)
        fuel_cost = round(dist_km * 2 * (1.80 / 4), 2)  # return journey

        assignments.append({
            **order,
            "assigned": True,
            "truck_id": best_truck["id"],
            "truck_reg": best_truck["reg"],
            "driver": best_truck["driver"],
            "distance_km": dist_km,
            "loads_needed": loads_needed,
            "dispatch_time": dispatch_time.strftime("%H:%M"),
            "arrival_time": arrival_time.strftime("%H:%M"),
            "pour_end_time": pour_end_time.strftime("%H:%M"),
            "return_time": return_time.strftime("%H:%M"),
            "dispatch_dt": dispatch_time.isoformat(),
            "return_dt": return_time.isoformat(),
            "fuel_cost_eur": fuel_cost,
            **slump
        })

    return assignments
