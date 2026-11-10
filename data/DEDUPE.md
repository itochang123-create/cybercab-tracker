# Parked-car / repeat-frame Cybercab dedupe

## Why
City of Austin cameras refresh about every 5 minutes. A parked or slowly moving
Cybercab can appear in many consecutive frames at the same camera and inflate
raw counts. Relative growth is less biased if each physical sighting streak
counts once.

## Rule (used by `peak_day_report.py`)
1. Restrict to the AM peak window (America/Chicago **07:00–09:00**).
2. For each camera, sort cycles by time.
3. For each Cybercab box in a cycle, try to match a track from the **previous
   cycle only** (adjacent 5‑min step):
   - match if **IoU ≥ 0.35**, OR
   - **normalized center distance ≤ 0.35**
     (distance / average box diagonal).
4. A match continues the streak (no new sighting). A non-match starts a new
   sighting. Tracks that miss a cycle die (no multi-gap association).
5. Daily metric = sum of unique streaks across cameras (= `cybercabs_deduped`).
   `per_1k_vehicles` / `per_1k_snapshots` use the deduped count.
   `cybercabs_raw` is kept for QA; `cybercabs` aliases the deduped value.

## Thresholds
| Parameter | Value | Rationale |
|---|---|---|
| IoU | 0.35 | Tolerates small pan/JPEG jitter; still separates lanes |
| Center dist / mean diagonal | 0.35 | Fallback when crop shrinks (partial occlusion) |
| Association | adjacent cycle only | 5‑min spacing; avoid linking different cars after a gap |

## Where it runs
- Wired in `peak_day_report.py` → `aggregate()` / `dedupe_parked()`.
- Detection stores `cybercab_boxes` (xyxy JSON) in `data/own_counts.csv`.
- Chart (`charts/make_peak_chart.py`) plots deduped per‑1k.

## Before / after (smoke on existing AM frames)
See notes appended below after the first validation run. Peak-panel AM data
from 2026-10-23 onward is the official series; earlier frames used a different
camera panel and are for method checks only.


## Smoke test (historical frames — method QA, not official series)
Official series starts 2026-10-23 with peak_geofence 81-cam AM-only panel.

| Slice | Raw frame-hits | Deduped | Reduction |
|---|---|---|---|
| All hours (any date, current own_counts) | 131 | 116 | 11.5% |
| AM peak window only (any date; was 07:30–09:00 at smoke) | 0 | 0 | 0.0% |

2026-10-20 AM cycles on the old 50-cam panel had **0** Cybercab hits, so that day
could not show a before/after delta. Deduplication is wired and will apply once
peak-window detections appear.
