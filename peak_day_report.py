#!/usr/bin/env python3
"""Daily Austin Cybercab Camera Monitor report.

After the AM peak window (America/Chicago 07:00–09:00):
  (a) ensure sampler health
  (b) run detection on today's peak-window frames if needed
  (c) append/update data/peak_daily.csv (raw + parked-car-deduped counts)
  (d) refresh charts/peak_monitor.png
  (e) print a short summary

Does not invent counts for a window that has not happened.
Usage:
  python3 peak_day_report.py [--date YYYY-MM-DD] [--force-detect] [--allow-partial]

v2 (2026-11-10) - the headline number (per_1k_vehicles) is computed exactly as before.
Added around it:
  - rate_lo95 / rate_hi95: 95% Poisson interval on the day's rate, so a day built
    on ~40-80 sightings is read with its real noise.
  - cams_with_cc: cameras that saw at least one Cybercab (a spread measure).
  - data/peak_by_camera*.csv: per-camera daily counts, the input for
    metrics_extra.py (spread vs density, matched cameras, corridors, re-pick).
  - own_counts.csv rows are keyed by frames dir as well as cycle + camera, so a core
    and an expanded cycle that start in the same second no longer overwrite each other.
  - CYBERCAB_ROOT env var overrides /workspace/cybercab (tests, other boxes).
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(os.environ.get("CYBERCAB_ROOT", "/workspace/cybercab"))
FRAMES = ROOT / "frames"
PANEL = ROOT / "sampler" / "panel.json"
CSV_PATH = ROOT / "data" / "peak_daily.csv"
OWN_COUNTS = ROOT / "data" / "own_counts.csv"
CHI = ZoneInfo("America/Chicago")
UTC = ZoneInfo("UTC")

# Window: America/Chicago 07:00–09:00 inclusive (HHMM)
# Extended from 07:30 start on 2026-10-27 (120 min for lower day noise).
AM_START = 7 * 60 + 0
AM_END = 9 * 60 + 0

FIELDS = [
    "date", "snapshots", "vehicles",
    "cybercabs_raw", "cybercabs_deduped", "cybercabs",
    "per_1k_vehicles", "per_1k_snapshots",
    "cams_ok", "cams_offline", "am_cycles", "notes",
    # v2 additions (appended so older rows / readers keep working)
    "cams_with_cc", "rate_lo95", "rate_hi95",
]
BY_CAM_FIELDS = ["date", "camera_id", "snapshots", "vehicles", "cybercabs_raw", "cybercabs_deduped"]
OWN_FIELDS = ["cycle_id", "camera_id", "fetched_at", "series_id",
              "vehicles", "cars_classified", "cybercabs", "cybercab_boxes", "frames"]
MIN_FULL_CYCLES = 18  # fewer AM cycles than this is flagged as a thin window


def open_text(path: Path, mode: str = "r"):
    """Open a CSV for text I/O; paths ending in .gz are gzip-compressed."""
    if str(path).endswith(".gz"):
        return gzip.open(path, mode + "t", newline="")
    return open(path, mode, newline="")


def poisson_ci(n: int, z: float = 1.96) -> tuple[float, float]:
    """95% interval for a Poisson count (Byar's approximation; exact enough for n >= 1)."""
    if n <= 0:
        return 0.0, 3.689  # exact upper bound for zero events
    lo = n * (1 - 1 / (9 * n) - z / (3 * n ** 0.5)) ** 3
    m = n + 1
    hi = m * (1 - 1 / (9 * m) + z / (3 * m ** 0.5)) ** 3
    return max(0.0, lo), hi


def frames_key(row: dict) -> str:
    """Frames dir a stored own_counts row belongs to (legacy rows have no column)."""
    f = row.get("frames")
    if f:
        return f
    return "frames_expanded" if row.get("series_id") == "peak_geofence_expanded" else "frames"


def by_camera_path() -> Path:
    return CSV_PATH.with_name(CSV_PATH.name.replace("peak_daily", "peak_by_camera"))


def austin_now():
    return datetime.now(CHI)


def cycle_austin_dt(cycle_id: str):
    try:
        dt = datetime.strptime(cycle_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        return dt.astimezone(CHI)
    except Exception:
        return None


def in_peak(dt: datetime) -> str | None:
    """Return 'am' if inside 07:00–09:00 America/Chicago, else None."""
    if os.environ.get("CYBERCAB_WINDOW_ALL"):  # smoke test: accept any time of day
        return "am"
    hm = dt.hour * 60 + dt.minute
    if AM_START <= hm <= AM_END:
        return "am"
    return None


def ensure_sampler_health():
    """Start cron if it is down, run every panel's watchdog, report heartbeat ages."""
    notes = []
    try:
        if subprocess.call(["pgrep", "-x", "cron"], stdout=subprocess.DEVNULL) != 0:
            subprocess.call(["sudo", "/usr/sbin/cron"])
            notes.append("started cron")
    except Exception as e:  # no pgrep/sudo on this box: report, do not crash the report
        notes.append(f"cron_check_failed({type(e).__name__})")
    sampler = ROOT / "sampler"
    for name, sfx in (("core", ""), ("expanded", "_expanded"), ("newarea", "_newarea")):
        if name == "newarea" and not (sampler / "panel_newarea.json").exists():
            continue
        wd = sampler / "panel_watchdog.sh"
        try:
            if wd.exists():
                subprocess.call(["bash", str(wd), name])
        except Exception as e:
            notes.append(f"{name}_watchdog_failed({type(e).__name__})")
        hb = sampler / f"heartbeat{sfx}"
        if hb.exists():
            age = int(datetime.now().timestamp() - hb.stat().st_mtime)
            # v2 loops touch the heartbeat every 30 s, even when idle
            notes.append(f"{name}_heartbeat_{'stale' if age > 300 else 'ok'}_{age}s")
        else:
            notes.append(f"{name}_no_heartbeat")
    return notes


def peak_cycles_for_date(austin_date: str):
    out = []
    if not FRAMES.exists():
        return out
    for d in sorted(FRAMES.iterdir()):
        if not d.is_dir():
            continue
        cid = d.name
        dt = cycle_austin_dt(cid)
        if not dt:
            continue
        if dt.strftime("%Y-%m-%d") != austin_date:
            continue
        w = in_peak(dt)
        if w:
            out.append((cid, w))
    return out


def panel_ids():
    panel = json.loads(PANEL.read_text())
    return {str(c["camera_id"]) for c in panel}


def run_detection(cycle_ids: list[str]):
    """Ensure det_cache + own_counts cover these cycles."""
    if not cycle_ids:
        return
    sys.path.insert(0, str(ROOT / "models"))
    import count_cybercabs as cc  # noqa

    jpgs = []
    for cid in cycle_ids:
        jpgs.extend(sorted((FRAMES / cid).glob("*.jpg")))
    if not jpgs:
        return

    clf = None
    try:
        import joblib
        clf = joblib.load(str(ROOT / "models" / "cybercab_clf.joblib"))
    except Exception as e:
        print("WARN no classifier:", e, flush=True)

    existing = {}
    if OWN_COUNTS.exists():
        with open_text(OWN_COUNTS) as f:
            for r in csv.DictReader(f):
                r["frames"] = frames_key(r)
                existing[(r["frames"], r["cycle_id"], r["camera_id"])] = r

    new_rows = []
    for k, j in enumerate(jpgs):
        key = (FRAMES.name, j.parent.name, j.stem)
        try:
            import numpy as np
            z = np.load(cc.process(str(j)))
        except Exception as e:
            print("ERR", j, e, flush=True)
            continue
        meta = {}
        meta_path = j.with_suffix(".json")
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
        n_cc = 0
        boxes = []
        if clf is not None and len(z["emb"]):
            p = clf["model"].predict_proba(z["emb"].astype("float32"))[:, 1]
            mask = p >= clf["threshold"]
            n_cc = int(mask.sum())
            # Persist Cybercab boxes for parked-car dedupe (xyxy)
            if "boxes" in z.files and n_cc:
                # emb rows map to boxes via emb_idx
                ei = z["emb_idx"][mask]
                bb = z["boxes"][ei]
                boxes = [[float(x) for x in row] for row in bb]
        row = dict(
            cycle_id=j.parent.name,
            camera_id=j.stem,
            fetched_at=meta.get("fetched_at", ""),
            series_id=meta.get("series_id", ""),
            vehicles=int(len(z["cls"])),
            cars_classified=int(len(z["emb"])),
            cybercabs=n_cc,
            cybercab_boxes=json.dumps(boxes),
            frames=FRAMES.name,
        )
        existing[key] = row
        new_rows.append(row)
        if k % 100 == 0:
            print(f"detect {k}/{len(jpgs)}", flush=True)

    all_rows = list(existing.values())
    all_rows.sort(key=lambda r: (r["cycle_id"], r["camera_id"], r["frames"]))
    OWN_COUNTS.parent.mkdir(parents=True, exist_ok=True)
    with open_text(OWN_COUNTS, "w") as f:
        w = csv.DictWriter(
            f,
            fieldnames=OWN_FIELDS,
            extrasaction="ignore",
        )
        w.writeheader()
        w.writerows(all_rows)
    print("own_counts rows", len(all_rows), "updated_this_run", len(new_rows), flush=True)


def load_counts_index():
    idx = {}
    if not OWN_COUNTS.exists():
        return idx
    with open_text(OWN_COUNTS) as f:
        for r in csv.DictReader(f):
            if frames_key(r) != FRAMES.name:
                continue
            idx[(r["cycle_id"], r["camera_id"])] = r
    return idx


def iou_xyxy(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def center_dist_norm(a, b) -> float:
    """Center distance normalized by average box diagonal (0 = same center)."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    acx, acy = (ax1 + ax2) / 2, (ay1 + ay2) / 2
    bcx, bcy = (bx1 + bx2) / 2, (by1 + by2) / 2
    dist = ((acx - bcx) ** 2 + (acy - bcy) ** 2) ** 0.5
    da = ((ax2 - ax1) ** 2 + (ay2 - ay1) ** 2) ** 0.5
    db = ((bx2 - bx1) ** 2 + (by2 - by1) ** 2) ** 0.5
    denom = (da + db) / 2 or 1.0
    return dist / denom


def dedupe_parked(cam_rows: list[dict], iou_thresh: float = 0.35, dist_thresh: float = 0.35) -> int:
    """Per-camera streak dedupe across consecutive cycles.

    cam_rows: list of {cycle_id, boxes: [[x1,y1,x2,y2], ...]} sorted by cycle time.
    A detection that matches a previous-cycle track (IoU >= iou_thresh OR
    normalized center distance <= dist_thresh) continues that streak and does
    not add a new sighting. Returns number of unique streaks (= deduped count).
    """
    # active tracks: list of last box
    tracks: list = []
    sightings = 0
    for row in cam_rows:
        boxes = row.get("boxes") or []
        matched = [False] * len(tracks)
        new_tracks = []
        for box in boxes:
            best_i, best_score = -1, -1.0
            for i, prev in enumerate(tracks):
                if matched[i]:
                    continue
                iou = iou_xyxy(box, prev)
                dist = center_dist_norm(box, prev)
                # Prefer IoU; fall back to center proximity
                ok = iou >= iou_thresh or dist <= dist_thresh
                score = iou if iou >= iou_thresh else (1.0 - dist)
                if ok and score > best_score:
                    best_i, best_score = i, score
            if best_i >= 0:
                matched[best_i] = True
                new_tracks.append(box)  # continue streak, no new sighting
            else:
                new_tracks.append(box)
                sightings += 1
        tracks = new_tracks
    return sightings


def aggregate(austin_date: str, cycles: list[tuple[str, str]], force_detect: bool):
    pids = panel_ids()
    cycle_ids = [c for c, _ in cycles]
    am_cycles = sorted({c for c, w in cycles if w == "am"})

    idx = load_counts_index()
    need = []
    for cid in cycle_ids:
        jpgs = list((FRAMES / cid).glob("*.jpg")) if (FRAMES / cid).exists() else []
        for j in jpgs:
            if pids and j.stem not in pids:
                continue
            if force_detect or (cid, j.stem) not in idx:
                need.append(cid)
                break
    if need or force_detect:
        run_detection(sorted(set(cycle_ids)))
        idx = load_counts_index()

    snapshots = 0
    vehicles = 0
    cybercabs_raw = 0
    cams_seen_ok = set()

    # Per-camera sequence for dedupe
    per_cam: dict[str, list] = {}
    cam_stats: dict[str, dict] = {}

    for cid, _w in cycles:
        cdir = FRAMES / cid
        if not cdir.exists():
            continue
        for j in sorted(cdir.glob("*.jpg")):
            cam = j.stem
            if pids and cam not in pids:  # one frames dir can serve several panels
                continue
            cams_seen_ok.add(cam)
            snapshots += 1
            cs = cam_stats.setdefault(cam, {"snapshots": 0, "vehicles": 0,
                                            "cybercabs_raw": 0, "cybercabs_deduped": 0})
            cs["snapshots"] += 1
            r = idx.get((cid, cam))
            if not r:
                continue
            vehicles += int(float(r.get("vehicles") or 0))
            n_cc = int(float(r.get("cybercabs") or 0))
            cybercabs_raw += n_cc
            cs["vehicles"] += int(float(r.get("vehicles") or 0))
            cs["cybercabs_raw"] += n_cc
            boxes = []
            raw_boxes = r.get("cybercab_boxes") or "[]"
            try:
                boxes = json.loads(raw_boxes) if isinstance(raw_boxes, str) else (raw_boxes or [])
            except Exception:
                boxes = []
            # If boxes missing but cybercabs>0, invent placeholder so count still
            # contributes as distinct (no cross-frame match possible).
            if n_cc and not boxes:
                boxes = [[i, i, i + 1, i + 1] for i in range(n_cc)]
            per_cam.setdefault(cam, []).append({"cycle_id": cid, "boxes": boxes})

    cybercabs_deduped = 0
    for cam, rows in per_cam.items():
        rows.sort(key=lambda x: x["cycle_id"])
        n_dedup = dedupe_parked(rows)
        cybercabs_deduped += n_dedup
        cam_stats[cam]["cybercabs_deduped"] = n_dedup
    cams_with_cc = sum(1 for v in cam_stats.values() if v["cybercabs_deduped"] > 0)
    lo, hi = poisson_ci(cybercabs_deduped)
    rate_lo = round(1000.0 * lo / vehicles, 3) if vehicles else 0.0
    rate_hi = round(1000.0 * hi / vehicles, 3) if vehicles else 0.0

    offline = sorted(pids - cams_seen_ok) if pids else []
    cams_ok = len(cams_seen_ok & pids) if pids else len(cams_seen_ok)

    # Primary metric uses deduped counts
    per_1k_v = round(1000.0 * cybercabs_deduped / vehicles, 3) if vehicles else 0.0
    per_1k_s = round(1000.0 * cybercabs_deduped / snapshots, 3) if snapshots else 0.0

    notes = []
    if not am_cycles:
        notes.append("am_window_missing")
    elif len(am_cycles) < MIN_FULL_CYCLES:
        notes.append(f"thin_cycles={len(am_cycles)}")
    if offline:
        notes.append(f"offline_cams={len(offline)}")
    if snapshots == 0:
        notes.append("no_snapshots")
    if cybercabs_raw != cybercabs_deduped:
        notes.append(f"dedupe_raw={cybercabs_raw}")

    return {
        "date": austin_date,
        "snapshots": snapshots,
        "vehicles": vehicles,
        "cybercabs_raw": cybercabs_raw,
        "cybercabs_deduped": cybercabs_deduped,
        "cybercabs": cybercabs_deduped,  # alias = primary metric
        "per_1k_vehicles": per_1k_v,
        "per_1k_snapshots": per_1k_s,
        "cams_ok": cams_ok,
        "cams_offline": len(offline),
        "am_cycles": len(am_cycles),
        "notes": ";".join(notes),
        "cams_with_cc": cams_with_cc,
        "rate_lo95": rate_lo,
        "rate_hi95": rate_hi,
        "_by_camera": cam_stats,
    }, offline


def upsert_by_camera(austin_date: str, cam_stats: dict):
    """Rewrite this date's per-camera rows in data/peak_by_camera*.csv."""
    path = by_camera_path()
    rows = []
    if path.exists():
        with path.open() as f:
            rows = [r for r in csv.DictReader(f) if r.get("date") != austin_date]
    for cam, v in cam_stats.items():
        rows.append({"date": austin_date, "camera_id": cam, **v})
    rows.sort(key=lambda r: (r["date"], str(r["camera_id"])))
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=BY_CAM_FIELDS)
        w.writeheader()
        w.writerows(rows)


def upsert_csv(row: dict):
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    if CSV_PATH.exists():
        with CSV_PATH.open() as f:
            rows = list(csv.DictReader(f))
    rows = [r for r in rows if r.get("date") != row["date"]]
    rows.append({k: row.get(k, "") for k in FIELDS})
    rows.sort(key=lambda r: r["date"])
    with CSV_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def print_summary(row: dict, all_rows: list[dict]):
    print("=== Peak monitor summary ===")
    print(f"date (America/Chicago): {row['date']}")
    print(f"snapshots={row['snapshots']} vehicles={row['vehicles']} "
          f"cybercabs_raw={row['cybercabs_raw']} cybercabs_deduped={row['cybercabs_deduped']}")
    print(f"per_1k_vehicles={row['per_1k_vehicles']}  per_1k_snapshots={row['per_1k_snapshots']} "
          f"(deduped)")
    print(f"95% interval on the day's rate: {row.get('rate_lo95')}-{row.get('rate_hi95')} per 1k vehicles "
          f"(Poisson, {row['cybercabs_deduped']} sightings)")
    print(f"cams_ok={row['cams_ok']} cams_offline={row['cams_offline']} "
          f"am_cycles={row['am_cycles']} cams_with_cybercab={row.get('cams_with_cc')}")
    print(f"notes: {row['notes'] or '(none)'}")
    prev = [r for r in all_rows if r["date"] < row["date"]]
    if prev:
        p = prev[-1]
        try:
            cur = float(row["per_1k_vehicles"] or 0) or float(row["per_1k_snapshots"] or 0)
            old = float(p["per_1k_vehicles"] or 0) or float(p["per_1k_snapshots"] or 0)
            if old:
                print(f"DoD change: {100 * (cur - old) / old:+.1f}% vs {p['date']} ({old})")
        except Exception:
            pass
    # Keep import local so this module stays runnable without weekday_ma on PATH edge cases
    from weekday_ma import weekday_5d_ma, all_days_7d_avg, _is_weekday
    # Like-for-like: a weekday against the previous weekday (Monday vs Friday, not vs Sunday).
    if _is_weekday(row["date"]):
        pw = [r for r in prev if _is_weekday(r["date"]) and float(r.get("vehicles") or 0) > 0]
        if pw and pw[-1]["date"] != prev[-1]["date"]:
            old = float(pw[-1]["per_1k_vehicles"] or 0)
            cur = float(row["per_1k_vehicles"] or 0)
            if old:
                print(f"vs previous weekday: {100 * (cur - old) / old:+.1f}% vs {pw[-1]['date']} ({old})")
    else:
        print("weekend/holiday: thin traffic inflates this rate; it is left out of the lead average")
    wd = weekday_5d_ma(all_rows, row["date"])
    if wd["n"] >= 1 and wd["ma"] is not None:
        line = f"weekday_5d_ma (n={wd['n']}): {wd['ma']:.3f}  dates={','.join(wd['dates'])}"
        if wd["change_pct"] is not None:
            line += (f"  vs prior weekday_5d {wd['prior_ma']:.3f} "
                     f"({wd['change_pct']:+.1f}%; {','.join(wd['prior_dates'])})")
        elif wd["n"] < 5:
            line += "  (fewer than 5 weekdays in series)"
        else:
            line += "  (prior non-overlapping weekday_5d N/A)"
        print(line)
        if wd.get("pooled") is not None:
            print(f"  pooled rate over those days: {wd['pooled']:.3f} "
                  f"(95% {wd['pooled_lo95']:.3f}-{wd['pooled_hi95']:.3f}; "
                  f"{wd['pooled_cybercabs']} sightings / {wd['pooled_vehicles']} vehicles)")
        if wd["n"] >= 3 and wd.get("ma_ex_max") is not None:
            print(f"  robustness: without the highest day ({wd['max_date']}, {wd['max_value']:.3f}) "
                  f"the average is {wd['ma_ex_max']:.3f}; median day {wd['median']:.3f}")
        if wd.get("excluded_holidays"):
            print(f"  holidays left out like weekends: {','.join(wd['excluded_holidays'])}")
    a7 = all_days_7d_avg(all_rows, row["date"])
    if a7["n"] >= 2 and a7["ma"] is not None:
        print(f"7d avg (n={a7['n']}): {a7['ma']:.3f}")


def main():
    global PANEL, FRAMES, CSV_PATH, OWN_COUNTS
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="Austin date YYYY-MM-DD (default: today America/Chicago)")
    ap.add_argument("--force-detect", action="store_true")
    ap.add_argument("--allow-partial", action="store_true",
                    help="Write row even if AM window incomplete")
    ap.add_argument("--panel", default=str(PANEL),
                    help="Panel JSON (default: core panel.json)")
    ap.add_argument("--frames", default=str(FRAMES),
                    help="Frames directory (default: frames/)")
    ap.add_argument("--csv", default=str(CSV_PATH),
                    help="Output CSV (default: data/peak_daily.csv)")
    ap.add_argument("--own-counts", default=str(OWN_COUNTS),
                    help="Per-frame counts CSV (.gz ok); default data/own_counts.csv")
    ap.add_argument("--skip-chart", action="store_true",
                    help="Skip refreshing peak_monitor.png")
    ap.add_argument("--skip-health", action="store_true",
                    help="Skip core sampler health check")
    args = ap.parse_args()

    PANEL = Path(args.panel)
    FRAMES = Path(args.frames)
    CSV_PATH = Path(args.csv)
    OWN_COUNTS = Path(args.own_counts)

    if not args.skip_health:
        health = ensure_sampler_health()
        print("health:", ", ".join(health), flush=True)
    else:
        print("health: skipped", flush=True)

    austin_date = args.date or austin_now().strftime("%Y-%m-%d")
    cycles = peak_cycles_for_date(austin_date)
    print(f"peak cycles for {austin_date}: {len(cycles)} (am only 07:00-09:00) "
          f"panel={PANEL.name} frames={FRAMES.name}",
          flush=True)

    now = austin_now()
    am_done = now.strftime("%Y-%m-%d") > austin_date or (
        now.strftime("%Y-%m-%d") == austin_date
        and (now.hour > 9 or (now.hour == 9 and now.minute > 0))
    )

    if not cycles:
        print(f"No peak-window frames yet for {austin_date}. "
              f"First report fires after AM window (~09:00 America/Chicago).")
        if not CSV_PATH.exists():
            CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
            with CSV_PATH.open("w", newline="") as f:
                csv.DictWriter(f, fieldnames=FIELDS).writeheader()
        return 0

    row, offline = aggregate(austin_date, cycles, args.force_detect)
    upsert_by_camera(austin_date, row.pop("_by_camera"))
    if (not am_done) and not args.allow_partial:
        if "am_window_missing" in row["notes"] or row["am_cycles"] == 0:
            print("AM window incomplete; writing partial row with notes "
                  "(pass --allow-partial to silence).")
    upsert_csv(row)
    if not args.skip_chart:  # chart reads both CSVs (expanded headline + core)
        subprocess.check_call([sys.executable, str(ROOT / "charts" / "make_peak_chart.py")])

    all_rows = list(csv.DictReader(CSV_PATH.open()))
    print_summary(row, all_rows)
    if offline and len(offline) <= 20:
        print("offline camera ids:", ",".join(offline))
    elif offline:
        print(f"offline camera ids (first 20): {','.join(offline[:20])} ...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
