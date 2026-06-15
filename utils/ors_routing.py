"""
utils/ors_routing.py

Fetches real road-following route geometry from OpenRouteService (ORS) for the
dispatch map.  Without this, the Folium map draws straight lines between the
plant and each delivery site — which is technically correct for distance estimation
but looks unconvincing to a client who knows the Irish road network.

ORS is free up to 2,000 requests/day.  Get a key at openrouteservice.org.
We use the 'driving-hgv' profile because transit mixers are heavy goods vehicles
and may face restrictions (low bridges, weight limits) that the HGV profile
accounts for.  Falls back to 'driving-car' if HGV returns an error (some rural
Irish routes have sparse HGV data).

The function returns a dict keyed by order_id so the map builder can look up
each route's geometry efficiently.
"""

import requests
from typing import Dict, List, Optional


ORS_BASE_URL = "https://api.openrouteservice.org/v2/directions"
# Heavy Goods Vehicle profile — correct for transit mixers (up to 32 tonnes)
PRIMARY_PROFILE   = "driving-hgv"
FALLBACK_PROFILE  = "driving-car"


def fetch_route_geometry(
    api_key: str,
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    profile: str = PRIMARY_PROFILE,
) -> Optional[List[List[float]]]:
    """
    Fetches the decoded polyline coordinates for a single plant→site route.

    ORS expects coordinates as [longitude, latitude] (GeoJSON convention —
    note this is the REVERSE of the Folium / Leaflet convention which uses
    [latitude, longitude]).  We do the swap here so callers can always pass
    coordinates in the intuitive lat/lon order.

    Returns a list of [lat, lon] pairs ready for folium.PolyLine, or None if
    the API call fails (network error, rate limit, unknown route).
    """
    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }
    body = {
        "coordinates": [
            [start_lon, start_lat],  # ORS wants [lon, lat]
            [end_lon,   end_lat],
        ],
        "format": "geojson",
        "instructions": False,       # we don't need turn-by-turn
        "geometry_simplify": False,  # keep full resolution for visual quality
    }

    try:
        response = requests.post(
            f"{ORS_BASE_URL}/{profile}/geojson",
            json=body,
            headers=headers,
            timeout=8,  # 8s timeout per route — ORS is usually < 1s
        )
        response.raise_for_status()
        data = response.json()

        # ORS returns GeoJSON LineString coordinates as [lon, lat] pairs.
        # Flip each pair to [lat, lon] for Folium.
        raw_coords = data["features"][0]["geometry"]["coordinates"]
        return [[lat, lon] for lon, lat in raw_coords]

    except requests.exceptions.HTTPError as e:
        if profile == PRIMARY_PROFILE and response.status_code in (400, 404):
            # HGV profile failed (common for remote Irish roads with sparse HGV data)
            # Try falling back to driving-car profile
            return fetch_route_geometry(api_key, start_lat, start_lon,
                                        end_lat, end_lon, profile=FALLBACK_PROFILE)
        print(f"ORS HTTP error for route ({start_lat},{start_lon})→({end_lat},{end_lon}): {e}")
        return None
    except Exception as e:
        print(f"ORS error: {e}")
        return None


def fetch_all_dispatch_routes(
    api_key: str,
    assignments: List[Dict],
    plant_lat: float = 53.9069,
    plant_lon: float = -6.8092,
) -> Dict[str, Optional[List[List[float]]]]:
    """
    Fetches real road geometry for all assigned dispatch routes.

    Returns a dict mapping order_id → list of [lat,lon] coordinate pairs.
    If a route fetch fails, the value is None (the map falls back to a straight line
    for that specific route, so one bad API call doesn't break the whole map).

    We fetch routes sequentially rather than in parallel to stay well within ORS
    rate limits.  For 10 deliveries, this takes ~5-10 seconds total.
    """
    route_geometries = {}

    for assignment in assignments:
        if not assignment.get("assigned"):
            continue

        order_id = assignment["order_id"]
        site_lat = assignment["site_lat"]
        site_lon = assignment["site_lon"]

        coords = fetch_route_geometry(
            api_key, plant_lat, plant_lon, site_lat, site_lon
        )
        route_geometries[order_id] = coords

    return route_geometries
