#!/usr/bin/env python3
"""Sample the Austin traffic cameras for one morning window (GitHub Actions job).

One process does the whole window: it waits until 07:00 America/Chicago, then
fetches every camera of every panel once per 5-minute slot until 09:00 and
exits. No supervisor, watchdog or cron is needed, because the job is started
fresh every day and a stuck fetch is bounded by per-request and per-cycle
deadlines.

Each camera is fetched once per cycle even if it belongs to several panels
(the 81 core cameras are all part of the 250 expanded cameras); the reports
pick out each panel's cameras afterwards.

Modes
  --gate            only decide whether a run should start now; prints go=true|false
  (default)         wait for 07:00 Chicago, sample until 09:00
  --smoke N         N cycles right now, 60 s apart, ignoring the window (pipeline test)

Exit codes: 0 ok, 1 nothing could be fetched (camera site unreachable).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import ssl
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(os.environ.get("CYBERCAB_ROOT") or Path(__file__).resolve().parent.parent)
CHI = ZoneInfo("America/Chicago")
WINDOW_START = (7, 0)          # America/Chicago, year-round
N_SLOTS = 24                   # 07:00, 07:05 ... 08:55
CADENCE_SEC = 300
EARLIEST_START = (5, 30)       # a run may start (and wait) from here ...
LATEST_START = (8, 30)         # ... until here; later than this the window is not worth sampling
STALE_MINUTES = 10
HTTP_TIMEOUT = 8.0
CYCLE_DEADLINE_SEC = 200.0
WORKERS = 24
MIN_FULL_CYCLES = 18
USER_AGENT = "AustinCybercabResearch/0.2 (City of Austin CCTV snapshots; research)"
DEFAULT_PANELS = ["sampler/panel_expanded.json", "sampler/panel.json", "sampler/panel_newarea.json"]


def chi_now() -> datetime:
    return datetime.now(CHI)


def load_cameras(panel_files: list[str]) -> list[dict]:
    """Union of all panels, one entry per camera, with the panels it belongs to."""
    cams: dict[str, dict] = {}
    for pf in panel_files:
        p = ROOT / pf
        if not p.exists():
            print(f"panel missing, skipped: {pf}", flush=True)
            continue
        for c in json.loads(p.read_text()):
            cid = str(c["camera_id"])
            e = cams.setdefault(cid, {
                "camera_id": cid,
                "location_name": c.get("location_name"),
                "url": c.get("screenshot_address") or f"https://cctv.austinmobility.io/image/{cid}.jpg",
                "series": [],
            })
            e["series"].append(c.get("series_id") or p.stem)
    tmpl = os.environ.get("CYBERCAB_CAMERA_URL_TEMPLATE")  # tests: e.g. http://127.0.0.1:8099/image/{id}.jpg
    if tmpl:
        for cid, e in cams.items():
            e["url"] = tmpl.format(id=cid)
    return list(cams.values())


def already_done(date: str) -> bool:
    """True if the headline series already has a full row for this date."""
    p = ROOT / "data" / "peak_daily_expanded.csv"
    if not p.exists():
        return False
    with p.open() as f:
        for r in csv.DictReader(f):
            if r.get("date") == date and int(float(r.get("am_cycles") or 0)) >= MIN_FULL_CYCLES:
                return True
    return False


def gate() -> tuple[bool, str]:
    now = chi_now()
    hm = (now.hour, now.minute)
    date = now.strftime("%Y-%m-%d")
    if hm < EARLIEST_START:
        return False, f"too early ({now:%H:%M} Chicago); a later scheduled run will take it"
    if hm >= LATEST_START:
        return False, f"too late ({now:%H:%M} Chicago) to sample today's 07:00-09:00 window"
    if already_done(date):
        return False, f"{date} already has a full row"
    return True, f"start: {now:%H:%M} Chicago, window opens 07:00"


def fetch_one(cam: dict, cycle_id: str, cycle_ts: datetime, cycle_dir: Path) -> dict:
    cid = cam["camera_id"]
    rec = {
        "cycle_id": cycle_id,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "camera_id": cid,
        "location_name": cam.get("location_name"),
        "series_id": "+".join(cam["series"]),
        "url": cam["url"],
        "status": None, "http_status": None, "last_modified": None,
        "age_seconds": None, "bytes": None, "error": None,
    }
    try:
        req = urllib.request.Request(cam["url"], headers={"User-Agent": USER_AGENT})
        ctx = ssl.create_default_context() if cam["url"].startswith("https") else None
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT, context=ctx) as resp:
            headers = {k.lower(): v for k, v in resp.headers.items()}
            body = resp.read()
            rec["http_status"] = resp.status
        lm = None
        if headers.get("last-modified"):
            try:
                lm = parsedate_to_datetime(headers["last-modified"]).astimezone(timezone.utc)
                rec["last_modified"] = lm.isoformat()
                rec["age_seconds"] = (cycle_ts - lm).total_seconds()
            except Exception:
                lm = None
        ctype = headers.get("content-type", "")
        if ctype and "image" not in ctype and len(body) < 2000:
            rec["status"] = "non_image"
        elif lm and lm < cycle_ts - timedelta(minutes=STALE_MINUTES):
            rec["status"] = "stale"
        elif len(body) < 500:
            rec["status"] = "too_small"
        else:
            (cycle_dir / f"{cid}.jpg").write_bytes(body)
            rec["bytes"] = len(body)
            rec["status"] = "saved"
            (cycle_dir / f"{cid}.json").write_text(json.dumps(rec))
    except Exception as e:  # network errors, HTTP errors, timeouts
        rec["status"] = "error"
        rec["error"] = f"{type(e).__name__}: {e}"[:200]
    return rec


def run_cycle(cams: list[dict], outdir: Path, log) -> dict:
    cycle_ts = datetime.now(timezone.utc)
    cycle_id = cycle_ts.strftime("%Y%m%dT%H%M%SZ")
    cycle_dir = outdir / cycle_id
    cycle_dir.mkdir(parents=True, exist_ok=True)
    counts = {"saved": 0, "stale": 0, "error": 0, "non_image": 0, "too_small": 0, "unfinished": 0}
    ex = ThreadPoolExecutor(max_workers=WORKERS)
    futs = [ex.submit(fetch_one, c, cycle_id, cycle_ts, cycle_dir) for c in cams]
    done, not_done = wait(futs, timeout=CYCLE_DEADLINE_SEC)
    for f in done:
        rec = f.result()
        counts[rec["status"]] = counts.get(rec["status"], 0) + 1
        log.write(json.dumps(rec) + "\n")
    counts["unfinished"] = len(not_done)
    ex.shutdown(wait=False, cancel_futures=True)
    summary = {"cycle_id": cycle_id, "chicago": cycle_ts.astimezone(CHI).strftime("%H:%M:%S"),
               "panel_size": len(cams), **counts,
               "seconds": round((datetime.now(timezone.utc) - cycle_ts).total_seconds(), 1)}
    (cycle_dir / "_cycle_summary.json").write_text(json.dumps(summary))
    print(json.dumps(summary), flush=True)
    log.flush()
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--smoke", type=int, default=0, help="run N cycles now, ignoring the window")
    ap.add_argument("--smoke-gap", type=float, default=60.0)
    ap.add_argument("--panels", nargs="*", default=DEFAULT_PANELS)
    ap.add_argument("--outdir", default=str(ROOT / "frames"))
    args = ap.parse_args()

    if args.gate:
        go, why = gate()
        print(f"go={'true' if go else 'false'}")
        print(why, file=sys.stderr)
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a") as f:
                f.write(f"go={'true' if go else 'false'}\n")
                f.write(f"date={chi_now():%Y-%m-%d}\n")
        return 0

    cams = load_cameras(args.panels)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"{len(cams)} cameras across {len(args.panels)} panels", flush=True)
    summaries = []
    with (outdir / "fetch_log.jsonl").open("a") as log:
        if args.smoke:
            for k in range(args.smoke):
                t0 = time.monotonic()
                summaries.append(run_cycle(cams, outdir, log))
                if k < args.smoke - 1:
                    time.sleep(max(0.0, args.smoke_gap - (time.monotonic() - t0)))
        else:
            now = chi_now()
            start = now.replace(hour=WINDOW_START[0], minute=WINDOW_START[1], second=0, microsecond=0)
            slots = [start + timedelta(seconds=CADENCE_SEC * k) for k in range(N_SLOTS)]
            print(f"window {slots[0]:%H:%M}-{slots[-1] + timedelta(seconds=CADENCE_SEC):%H:%M} Chicago; "
                  f"now {now:%H:%M:%S}", flush=True)
            for slot in slots:
                delay = (slot - chi_now()).total_seconds()
                if delay < -0.8 * CADENCE_SEC:
                    print(f"slot {slot:%H:%M} missed (started late)", flush=True)
                    continue
                while delay > 0:  # sleep in short steps so the log shows the job is alive
                    time.sleep(min(delay, 60))
                    delay = (slot - chi_now()).total_seconds()
                summaries.append(run_cycle(cams, outdir, log))

    total_saved = sum(s["saved"] for s in summaries)
    window = {"cycles": len(summaries), "cameras": len(cams), "frames_saved": total_saved,
              "per_cycle_saved": [s["saved"] for s in summaries]}
    (outdir / "_window_summary.json").write_text(json.dumps(window))
    print("window summary:", json.dumps(window), flush=True)
    if total_saved == 0:
        print("ERROR: no frames saved - the camera site is unreachable from this runner", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
