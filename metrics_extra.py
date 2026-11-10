#!/usr/bin/env python3
"""Extra measurements on top of the headline rate (context, never the lead number).

Reads data/peak_by_camera_expanded.csv (written by peak_day_report.py) and answers:
  1. Spread or density?  Sightings = cameras that saw a Cybercab x sightings per
     such camera. Growth in the first is the fleet reaching more places; growth
     in the second is more cars on the same streets.
  2. Is it a camera-mix effect?  The same rate recomputed on "matched" cameras,
     i.e. only cameras that reported on every day being compared, so cameras
     going offline or coming back cannot move the number.
  3. Is it concentrated?  Share of sightings from the top 10 cameras, and how
     many cameras saw a Cybercab that saw none in the baseline.
  4. Where?  Distance bands from downtown, and named corridors.
  5. New service area: counts from the new-area panel, if it is running.

The comparison is the given date (default: latest weekday) against the
BASELINE = the five counted weekdays before it. Weekends and listed holidays
are skipped on both sides (see weekday_ma.py).

Usage
  python3 metrics_extra.py [--date YYYY-MM-DD]     # prints + writes reports/extras_<date>.md
  python3 metrics_extra.py --backfill               # rebuild the per-camera CSV from own_counts.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(os.environ.get("CYBERCAB_ROOT", "/workspace/cybercab"))
sys.path.insert(0, str(ROOT))
from weekday_ma import _is_weekday, _poisson_ci  # noqa: E402

_dd = os.environ.get("CYBERCAB_DATA_DIR")
DATA = (ROOT / _dd) if _dd else ROOT / "data"
_rd = os.environ.get("CYBERCAB_REPORTS_DIR")
REPORTS = (ROOT / _rd) if _rd else ROOT / "reports"
CHI = ZoneInfo("America/Chicago")
DOWNTOWN = (30.2672, -97.7431)
BANDS = [(0, 3, "0-3 km (downtown)"), (3, 8, "3-8 km"), (8, 99, "8+ km (edge of original area)")]
# First match wins; names are matched against the camera's location_name.
CORRIDORS = [
    ("I-35", (" 35 ", "IH 35", "IH-35", "I-35", " 35 SVRD", "INTERSTATE 35")),
    ("MoPac", ("MOPAC", "LOOP 1")),
    ("US-183", ("183",)),
    ("Lamar", ("LAMAR",)),
    ("Congress", ("CONGRESS",)),
    ("Guadalupe", ("GUADALUPE",)),
    ("Burnet", ("BURNET",)),
    ("Riverside", ("RIVERSIDE",)),
    ("Cesar Chavez", ("CESAR CHAVEZ",)),
    ("Airport Blvd", ("AIRPORT",)),
    ("Parmer", ("PARMER",)),
    ("Ben White / SH-71", ("BEN WHITE", " 71 ", "SH 71")),
]


def km_from_downtown(lat: float, lon: float) -> float:
    dy = (lat - DOWNTOWN[0]) * 110.57
    dx = (lon - DOWNTOWN[1]) * 111.32 * math.cos(math.radians(DOWNTOWN[0]))
    return math.hypot(dx, dy)


def corridor_of(name: str) -> str:
    n = f" {(name or '').upper()} "
    for label, keys in CORRIDORS:
        if any(k in n for k in keys):
            return label
    return "Other"


def band_of(km: float) -> str:
    for lo, hi, label in BANDS:
        if lo <= km < hi:
            return label
    return BANDS[-1][2]


def load_by_camera(path: Path) -> dict[str, dict[str, dict]]:
    """{date: {camera_id: {snapshots, vehicles, cybercabs}}}"""
    out: dict[str, dict[str, dict]] = defaultdict(dict)
    if not path.exists():
        return out
    with path.open() as f:
        for r in csv.DictReader(f):
            out[r["date"]][str(r["camera_id"])] = {
                "snapshots": int(r["snapshots"]),
                "vehicles": int(r["vehicles"]),
                "cybercabs": int(r["cybercabs_deduped"]),
            }
    return out


def backfill(frames_name: str, out_path: Path) -> None:
    """Rebuild the per-camera CSV from own_counts.csv (no frames needed)."""
    sys.path.insert(0, str(ROOT))
    import peak_day_report as pdr

    csv.field_size_limit(10 ** 9)
    per: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    with (ROOT / "data" / "own_counts.csv").open() as f:
        for r in csv.DictReader(f):
            if pdr.frames_key(r) != frames_name:
                continue
            dt = pdr.cycle_austin_dt(r["cycle_id"])
            if not dt or not pdr.in_peak(dt):
                continue
            per[dt.strftime("%Y-%m-%d")][r["camera_id"]].append(r)
    rows = []
    for day in sorted(per):
        for cam, rs in per[day].items():
            rs.sort(key=lambda x: x["cycle_id"])
            seq = [{"cycle_id": x["cycle_id"], "boxes": json.loads(x.get("cybercab_boxes") or "[]")} for x in rs]
            rows.append({
                "date": day, "camera_id": cam, "snapshots": len(rs),
                "vehicles": sum(int(float(x["vehicles"] or 0)) for x in rs),
                "cybercabs_raw": sum(int(float(x["cybercabs"] or 0)) for x in rs),
                "cybercabs_deduped": pdr.dedupe_parked(seq),
            })
    rows.sort(key=lambda r: (r["date"], str(r["camera_id"])))
    with out_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=pdr.BY_CAM_FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"backfilled {out_path} ({len(rows)} rows, {len(per)} days)")


def agg(days: list[str], data: dict, cams: set[str] | None = None) -> dict:
    """Pool counts over days (optionally restricted to a camera set)."""
    cabs = veh = snaps = 0
    active_cam_days = reporting_cam_days = 0
    for d in days:
        for cam, v in data[d].items():
            if cams is not None and cam not in cams:
                continue
            cabs += v["cybercabs"]
            veh += v["vehicles"]
            snaps += v["snapshots"]
            reporting_cam_days += 1
            active_cam_days += 1 if v["cybercabs"] > 0 else 0
    n = max(1, len(days))
    lo, hi = _poisson_ci(cabs)
    return {
        "days": len(days), "cabs": cabs, "veh": veh, "snaps": snaps,
        "cabs_per_day": cabs / n, "veh_per_day": veh / n,
        "rate": 1000.0 * cabs / veh if veh else 0.0,
        "rate_lo": 1000.0 * lo / veh if veh else 0.0,
        "rate_hi": 1000.0 * hi / veh if veh else 0.0,
        "active_per_day": active_cam_days / n,
        "reporting_per_day": reporting_cam_days / n,
        "cabs_per_active": cabs / active_cam_days if active_cam_days else 0.0,
    }


def pct(new: float, old: float) -> str:
    if not old:
        return "n/a"
    return f"{100.0 * (new - old) / old:+.0f}%"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="Austin date (default: latest counted weekday in the data)")
    ap.add_argument("--by-camera", default=str(DATA / "peak_by_camera_expanded.csv"))
    ap.add_argument("--panel", default=str(ROOT / "sampler" / "panel_expanded.json"))
    ap.add_argument("--backfill", action="store_true",
                    help="rebuild --by-camera from data/own_counts.csv first")
    ap.add_argument("--frames-name", default="frames_expanded")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()

    by_cam_path = Path(args.by_camera)
    if args.backfill:
        backfill(args.frames_name, by_cam_path)
    data = load_by_camera(by_cam_path)
    weekdays = sorted(d for d in data if _is_weekday(d))
    if not weekdays:
        print("no weekday data yet in", by_cam_path)
        return 0
    day = args.date or weekdays[-1]
    if day not in data:
        print(f"no per-camera data for {day}")
        return 0
    base_days = [d for d in weekdays if d < day][-5:]
    L: list[str] = []
    out = L.append
    dow = datetime.strptime(day, "%Y-%m-%d").strftime("%a")
    out(f"# Extra measurements - {day} ({dow})")
    out("")
    if not _is_weekday(day):
        out("_This date is a weekend or holiday; thin traffic inflates rates. Shown for completeness._")
        out("")
    if len(base_days) < 3:
        out(f"Baseline needs at least 3 earlier weekdays; only {len(base_days)} exist. Skipping comparisons.")
        print("\n".join(L))
        return 0
    out(f"Baseline = previous {len(base_days)} weekdays ({base_days[0]} to {base_days[-1]}). "
        "Counts are parked-car-deduped sightings, not unique cars.")
    out("")

    cur, base = agg([day], data), agg(base_days, data)

    # 1. Rate and its parts
    out("## 1. What moved")
    out("")
    out("| | Baseline (per day) | " + day + " | Change |")
    out("| --- | ---: | ---: | ---: |")
    out(f"| Cybercabs per 1,000 vehicles | {base['rate']:.3f} | {cur['rate']:.3f} | {pct(cur['rate'], base['rate'])} |")
    out(f"| Sightings | {base['cabs_per_day']:.1f} | {cur['cabs']} | {pct(cur['cabs'], base['cabs_per_day'])} |")
    out(f"| Vehicles counted | {base['veh_per_day']:,.0f} | {cur['veh']:,} | {pct(cur['veh'], base['veh_per_day'])} |")
    out(f"| Cameras reporting | {base['reporting_per_day']:.0f} | {cur['reporting_per_day']:.0f} | |")
    out(f"| Cameras with a sighting (spread) | {base['active_per_day']:.1f} | {cur['active_per_day']:.0f} | {pct(cur['active_per_day'], base['active_per_day'])} |")
    out(f"| Sightings per such camera (density) | {base['cabs_per_active']:.2f} | {cur['cabs_per_active']:.2f} | {pct(cur['cabs_per_active'], base['cabs_per_active'])} |")
    out("")
    out(f"95% interval on the day's rate: {cur['rate_lo']:.3f}-{cur['rate_hi']:.3f}; "
        f"on the baseline: {base['rate_lo']:.3f}-{base['rate_hi']:.3f}. "
        + ("The intervals do not overlap, so the change is larger than counting noise."
           if cur["rate_lo"] > base["rate_hi"] or cur["rate_hi"] < base["rate_lo"]
           else "The intervals overlap, so the change is within counting noise."))
    if base["active_per_day"] and base["cabs_per_active"] and cur["cabs"] and base["cabs"]:
        ls = math.log(cur["active_per_day"] / base["active_per_day"])
        ld = math.log(cur["cabs_per_active"] / base["cabs_per_active"])
        tot = ls + ld
        if abs(tot) > 0.02:
            out(f"Of the change in sightings, about {100 * ls / tot:.0f}% is spread (more cameras seeing one) "
                f"and {100 * ld / tot:.0f}% is density (more per camera).")
    out("")

    # 2. Matched cameras
    matched = set(data[day])
    for d in base_days:
        matched &= {c for c, v in data[d].items() if v["snapshots"] > 0}
    mc, mb = agg([day], data, matched), agg(base_days, data, matched)
    out("## 2. Same cameras only")
    out("")
    out(f"{len(matched)} cameras reported on every one of these days. On those alone the rate went "
        f"{mb['rate']:.3f} -> {mc['rate']:.3f} ({pct(mc['rate'], mb['rate'])}), against "
        f"{pct(cur['rate'], base['rate'])} on the full panel. "
        + ("Camera mix is not driving the change." if abs((mc["rate"] / mb["rate"] if mb["rate"] else 1)
                                                         - (cur["rate"] / base["rate"] if base["rate"] else 1)) < 0.10
           else "Part of the change comes from which cameras were online."))
    out("")

    # 3. Concentration
    ranked = sorted(data[day].items(), key=lambda kv: -kv[1]["cybercabs"])
    top10 = sum(v["cybercabs"] for _, v in ranked[:10])
    seen_base = {c for d in base_days for c, v in data[d].items() if v["cybercabs"] > 0}
    seen_day = {c for c, v in data[day].items() if v["cybercabs"] > 0}
    new_cams = seen_day - seen_base
    panel = {}
    pp = Path(args.panel)
    if pp.exists():
        panel = {str(c["camera_id"]): c for c in json.loads(pp.read_text())}
    out("## 3. Concentration")
    out("")
    out(f"Top 10 cameras hold {100 * top10 / cur['cabs'] if cur['cabs'] else 0:.0f}% of the day's sightings. "
        f"{len(new_cams)} of the {len(seen_day)} cameras with a sighting had none in the baseline.")
    out("")
    out("| Camera | Location | Sightings | Baseline per day |")
    out("| --- | --- | ---: | ---: |")
    for cam, v in ranked[:8]:
        if not v["cybercabs"]:
            break
        b = sum(data[d].get(cam, {}).get("cybercabs", 0) for d in base_days) / len(base_days)
        out(f"| {cam} | {(panel.get(cam, {}).get('location_name') or '').strip()} | {v['cybercabs']} | {b:.1f} |")
    out("")

    # 4. Where
    if panel:
        def group(keyfn):
            g: dict[str, set] = defaultdict(set)
            for cam, c in panel.items():
                g[keyfn(c)].add(cam)
            return g

        bands = group(lambda c: band_of(km_from_downtown(c["lat"], c["lon"])))
        out("## 4. Where")
        out("")
        out("| Distance from downtown | Cameras | Baseline rate | " + day + " rate | Change | Sightings |")
        out("| --- | ---: | ---: | ---: | ---: | ---: |")
        for _, _, label in BANDS:
            cams = bands.get(label, set())
            if not cams:
                continue
            c, b = agg([day], data, cams), agg(base_days, data, cams)
            out(f"| {label} | {len(cams)} | {b['rate']:.3f} | {c['rate']:.3f} | {pct(c['rate'], b['rate'])} | {c['cabs']} |")
        out("")
        cors = group(lambda c: corridor_of(c.get("location_name") or ""))
        rows = []
        for label, cams in cors.items():
            c, b = agg([day], data, cams), agg(base_days, data, cams)
            if b["veh"] + c["veh"] == 0:
                continue
            rows.append((label, len(cams), b, c))
        rows.sort(key=lambda r: -r[3]["cabs"])
        out("| Corridor | Cameras | Baseline rate | " + day + " rate | Sightings (baseline per day -> day) |")
        out("| --- | ---: | ---: | ---: | ---: |")
        for label, n, b, c in rows:
            out(f"| {label} | {n} | {b['rate']:.3f} | {c['rate']:.3f} | {b['cabs_per_day']:.1f} -> {c['cabs']} |")
        out("")
        out("_Corridor and band cells rest on a handful of sightings each; read them as direction, not size._")
        out("")

    # 5. New service area
    na_path = DATA / "peak_by_camera_newarea.csv"
    out("## 5. New service area (outside the original boundary)")
    out("")
    na = load_by_camera(na_path)
    if day in na:
        n = agg([day], na)
        hist = [d for d in sorted(na) if d < day and _is_weekday(d)][-5:]
        line = (f"{n['cabs']} sightings at {n['active_per_day']:.0f} of {n['reporting_per_day']:.0f} reporting cameras, "
                f"{n['veh']:,} vehicles ({n['rate']:.3f} per 1,000).")
        if hist:
            h = agg(hist, na)
            line += f" Previous {len(hist)} weekdays: {h['cabs_per_day']:.1f} sightings and {h['active_per_day']:.1f} cameras per day."
        out(line)
        both_c, both_v = cur["cabs"] + n["cabs"], cur["veh"] + n["veh"]
        if both_v:
            out("")
            out(f"Old and new area together: {1000.0 * both_c / both_v:.3f} per 1,000 vehicles "
                f"({both_c} sightings). Context only; the headline stays on the original-area panel.")
    else:
        out("No new-area data for this date yet. The headline covers the original area only, so it "
            "can flatten while the fleet spreads into the new area.")
    out("")

    text = "\n".join(L)
    print(text)
    if not args.no_write:
        rp = REPORTS / f"extras_{day}.md"
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(text + "\n")
        print(f"(wrote {rp})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
