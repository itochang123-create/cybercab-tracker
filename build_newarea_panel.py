#!/usr/bin/env python3
"""Build the new-service-area camera panel (sampler/panel_newarea.json).

Tesla expanded the Austin service area on 2026-11-09. The core and expanded
panels only hold cameras inside the ORIGINAL area, so they measure density
there and miss cars that now work the new ground. This panel covers the rest.

Selection
  1. Start from every TURNED_ON City of Austin camera (data/cameras_on.json).
  2. Drop cameras inside the original Tesla boundary (tesla:austin polygon in
     data/areas.json) - those are the pool the existing panels were picked from.
  3. If --new-geojson is given (the new boundary, once it is published), keep
     only cameras inside it. Without it, every camera outside the original
     area is kept ("ring" panel): a camera outside Tesla's real new boundary
     simply never sees a Cybercab, which costs sampling effort but cannot
     create false growth, because this panel is reported as counts and
     cameras-with-a-sighting, never blended into the headline rate.
  4. Rank by 7-day traffic (data/area_stats_7d.json) and keep --cap cameras.

Usage
  python3 build_newarea_panel.py                      # ring panel, all cameras
  python3 build_newarea_panel.py --new-geojson data/tesla_austin_2026-11-09.geojson
  python3 build_newarea_panel.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("CYBERCAB_ROOT", "/workspace/cybercab"))
DOWNTOWN = (30.2672, -97.7431)


def polygons(geo: dict) -> list:
    """Return a list of polygons (each a list of rings) from any GeoJSON object."""
    t = geo.get("type")
    if t == "FeatureCollection":
        out = []
        for f in geo["features"]:
            out += polygons(f)
        return out
    if t == "Feature":
        return polygons(geo["geometry"])
    if t == "Polygon":
        return [geo["coordinates"]]
    if t == "MultiPolygon":
        return list(geo["coordinates"])
    raise ValueError(f"unsupported GeoJSON type {t}")


def in_ring(lon: float, lat: float, ring: list) -> bool:
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def in_polys(lon: float, lat: float, polys: list) -> bool:
    for poly in polys:
        if in_ring(lon, lat, poly[0]) and not any(in_ring(lon, lat, h) for h in poly[1:]):
            return True
    return False


def area_sq_mi(polys: list) -> float:
    total = 0.0
    for poly in polys:
        for k, ring in enumerate(poly):
            lat0 = sum(p[1] for p in ring) / len(ring)
            kx, ky = 111.32 * math.cos(math.radians(lat0)), 110.57
            a = 0.0
            for i in range(len(ring)):
                x1, y1 = ring[i][0] * kx, ring[i][1] * ky
                x2, y2 = ring[(i + 1) % len(ring)][0] * kx, ring[(i + 1) % len(ring)][1] * ky
                a += x1 * y2 - x2 * y1
            a = abs(a) / 2 / 2.58999
            total += a if k == 0 else -a
    return total


def km_from_downtown(lat: float, lon: float) -> float:
    dy = (lat - DOWNTOWN[0]) * 110.57
    dx = (lon - DOWNTOWN[1]) * 111.32 * math.cos(math.radians(DOWNTOWN[0]))
    return math.hypot(dx, dy)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--new-geojson", help="GeoJSON of the NEW Tesla Austin boundary (optional)")
    ap.add_argument("--cap", type=int, default=0, help="max cameras (0 = all eligible)")
    ap.add_argument("--out", default=str(ROOT / "sampler" / "panel_newarea.json"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    areas = json.loads((ROOT / "data" / "areas.json").read_text())["areas"]
    old = polygons(next(a for a in areas if a["key"] == "tesla:austin")["boundary"])
    new = polygons(json.loads(Path(args.new_geojson).read_text())) if args.new_geojson else None

    traffic = {}
    stats = ROOT / "data" / "area_stats_7d.json"
    if stats.exists():
        for c in json.loads(stats.read_text())["cameras"]:
            traffic[str(c["id"])] = (c.get("traffic") or 0, c.get("observations") or 0)

    already = set()
    for name in ("panel.json", "panel_expanded.json"):
        p = ROOT / "sampler" / name
        if p.exists():
            already |= {str(c["camera_id"]) for c in json.loads(p.read_text())}

    cams = json.loads((ROOT / "data" / "cameras_on.json").read_text())
    picked, n_old, n_outside_new = [], 0, 0
    for c in cams:
        loc = c.get("location") or {}
        if c.get("camera_status") != "TURNED_ON" or not loc.get("coordinates"):
            continue
        lon, lat = loc["coordinates"][:2]
        cid = str(c["camera_id"])
        if in_polys(lon, lat, old) or cid in already:
            n_old += 1
            continue
        if new is not None and not in_polys(lon, lat, new):
            n_outside_new += 1
            continue
        t, obs = traffic.get(cid, (0, 0))
        picked.append({
            "camera_id": cid,
            "series_id": "newarea" if new is not None else "newarea_ring",
            "location_name": c.get("location_name"),
            "primary_st": c.get("primary_st"),
            "cross_st": c.get("cross_st"),
            "lat": lat,
            "lon": lon,
            "location": loc,
            "camera_status": c.get("camera_status"),
            "screenshot_address": c.get("screenshot_address")
            or f"https://cctv.austinmobility.io/image/{cid}.jpg",
            "traffic_7d": t,
            "obs_7d": obs,
            "km_from_downtown": round(km_from_downtown(lat, lon), 1),
            "score": t,
            "source": "inside new boundary, outside original" if new is not None
            else "outside original boundary (ring; new boundary not yet published)",
            "in_core_panel": False,
        })
    picked.sort(key=lambda c: (-c["score"], c["camera_id"]))
    if args.cap:
        picked = picked[: args.cap]

    print(f"original area: {area_sq_mi(old):.0f} sq mi" + (f"; new area: {area_sq_mi(new):.0f} sq mi" if new else ""))
    print(f"turned-on cameras: {len(cams)}; inside original area or already in a panel: {n_old}"
          + (f"; outside the new boundary: {n_outside_new}" if new is not None else ""))
    print(f"selected for the new-area panel: {len(picked)}"
          + ("" if new is not None else "  (ring panel - rerun with --new-geojson once the boundary is published)"))
    for c in picked[:8]:
        print(f"  {c['camera_id']:>5}  {c['km_from_downtown']:>5} km  traffic_7d={c['traffic_7d']:<6} {(c['location_name'] or '').strip()}")
    if args.dry_run:
        return 0

    Path(args.out).write_text(json.dumps(picked, indent=1))
    sel = {
        "series_id": picked[0]["series_id"] if picked else "newarea_ring",
        "method": "TURNED_ON City of Austin cameras outside the original Tesla Austin boundary"
        + (" and inside the new boundary" if new is not None else " (ring; new boundary unknown)"),
        "new_geojson": args.new_geojson,
        "original_area_sq_mi": round(area_sq_mi(old), 1),
        "new_area_sq_mi": round(area_sq_mi(new), 1) if new else None,
        "selected": len(picked),
        "ranking": "area_stats_7d traffic desc",
        "frames_dir": "frames_newarea",
        "csv": "data/peak_daily_newarea.csv",
        "reporting": "counts and cameras-with-a-sighting only; never blended into the headline rate",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "camera_ids": [c["camera_id"] for c in picked],
    }
    (ROOT / "data" / "newarea_panel_selection.json").write_text(json.dumps(sel, indent=1))
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
