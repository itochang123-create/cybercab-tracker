#!/usr/bin/env python3
"""Run detection on one morning's frames and save small crops of every Cybercab hit.

Detection itself is the unchanged code path of the original tracker
(peak_day_report.run_detection -> models/count_cybercabs.py: YOLOv8n vehicles,
CLIP ViT-B-32 features, logistic-regression Cybercab classifier at its stored
threshold). The crops are for auditing false positives; they are uploaded as a
workflow artifact, not committed.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("CYBERCAB_ROOT") or Path(__file__).resolve().parent.parent)
os.environ.setdefault("CYBERCAB_ROOT", str(ROOT))
sys.path.insert(0, str(ROOT))
import peak_day_report as pdr  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="Austin date YYYY-MM-DD")
    ap.add_argument("--frames", default=str(ROOT / "frames"))
    ap.add_argument("--own-counts", required=True)
    ap.add_argument("--evidence-dir", default="")
    args = ap.parse_args()

    pdr.FRAMES = Path(args.frames)
    pdr.OWN_COUNTS = Path(args.own_counts)
    cycles = [c for c, _ in pdr.peak_cycles_for_date(args.date)]
    n_frames = sum(len(list((pdr.FRAMES / c).glob("*.jpg"))) for c in cycles)
    print(f"detect: {len(cycles)} cycles, {n_frames} frames for {args.date}", flush=True)
    if not cycles:
        return 0
    t0 = time.time()
    pdr.run_detection(cycles)
    print(f"detect: done in {time.time() - t0:.0f}s ({(time.time() - t0) / max(1, n_frames):.2f}s per frame)", flush=True)

    if args.evidence_dir:
        from PIL import Image
        ev = Path(args.evidence_dir)
        ev.mkdir(parents=True, exist_ok=True)
        n = 0
        with pdr.open_text(pdr.OWN_COUNTS) as f:
            for r in csv.DictReader(f):
                if r["cycle_id"] not in cycles or not int(float(r.get("cybercabs") or 0)):
                    continue
                jpg = pdr.FRAMES / r["cycle_id"] / f"{r['camera_id']}.jpg"
                if not jpg.exists():
                    continue
                im = Image.open(jpg).convert("RGB")
                for i, (x1, y1, x2, y2) in enumerate(json.loads(r.get("cybercab_boxes") or "[]")):
                    w, h = x2 - x1, y2 - y1
                    crop = im.crop((max(0, x1 - 0.5 * w), max(0, y1 - 0.5 * h),
                                    min(im.width, x2 + 0.5 * w), min(im.height, y2 + 0.5 * h)))
                    crop.thumbnail((320, 320))
                    crop.save(ev / f"{r['cycle_id']}_cam{r['camera_id']}_{i}.jpg", quality=85)
                    n += 1
        print(f"evidence: saved {n} Cybercab crops to {ev}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
