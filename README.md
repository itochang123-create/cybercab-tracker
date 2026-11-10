# Austin Cybercab tracker

Measures how fast Tesla's Cybercab fleet in Austin is growing, from City of Austin
public traffic cameras. It reports **relative growth only**: Cybercabs per 1,000
vehicles seen, and % change. It does not estimate fleet size or miles.

**Today's numbers: [LATEST.md](LATEST.md)** (machine-readable: `data/latest.json`).

## How it runs
One GitHub Actions job a day (`.github/workflows/daily.yml`) does everything:

1. **Sample** (`tracker/run_window.py`): every camera of every panel, once per 5 minutes,
   07:00-09:00 America/Chicago (24 cycles). Frames whose Last-Modified is older than
   10 minutes are dropped. The window is evaluated in Chicago time, so US daylight
   saving needs no changes.
2. **Detect** (`tracker/detect_day.py`, `models/count_cybercabs.py`): YOLOv8n finds every
   vehicle (the denominator); a CLIP-feature classifier picks out Cybercabs.
3. **Report** (`peak_day_report.py`, `weekday_ma.py`, `metrics_extra.py`): parked-car
   dedupe, daily rate per panel, the lead number, error bars and the extra measurements.
4. **Publish** (`tracker/make_digest.py`): `LATEST.md`, `reports/digest_<date>.md`,
   `data/latest.json`, `charts/peak_monitor.png`, committed back to this repository.

Camera frames are not stored. Small crops of each detected Cybercab are kept for 90 days
as a workflow artifact, to audit false positives.

Run it by hand from the Actions tab ("Run workflow"): `smoke` fetches two quick cycles at
any time of day and runs the full pipeline without touching the series; `window` is the
real run and only starts between 05:30 and 08:30 Chicago time.

## Panels
| Panel | File | Cameras | Role | Series starts |
| --- | --- | ---: | --- | --- |
| Expanded | `sampler/panel_expanded.json` | 250 | **Headline** | 2026-10-29 |
| Core | `sampler/panel.json` | 81 | Legacy series, kept for continuity | 2026-10-23 |
| New area | `sampler/panel_newarea.json` | 98 | Context: spread into the new service area | first GitHub run |

All 81 core cameras are among the 250 expanded cameras, so each camera is fetched once
per cycle and the core series is computed from the same frames.

**Service-area caveat (since 2026-11-09):** Tesla expanded the Austin service area. The
core and expanded panels sit inside the original area (263 sq mi in `data/areas.json`),
so the headline tracks density there. The new-area panel is every turned-on City camera
outside that boundary; it is reported next to the headline and never blended into it.
When the new boundary is published, rebuild it with
`python3 build_newarea_panel.py --new-geojson <file>`.

## What gets reported
- **Lead number:** weekday 5-day moving average of the expanded panel (the last five
  Monday-Friday mornings). Weekends and listed holidays (`HOLIDAYS` in `weekday_ma.py`)
  are reported but left out, because thin traffic inflates the rate.
- **With it:** the pooled rate over the same five days with a 95% interval, and the
  average without its highest day, so a one-day spike is visible as such.
- **Secondary:** all-days 7-day average, day-over-day change, change vs the previous weekday.
- **What moved:** spread vs density, same-cameras-only rate, concentration, distance
  bands and corridors, new-area counts.

## Data
| File | Contents |
| --- | --- |
| `data/peak_daily_expanded.csv` | Headline daily series |
| `data/peak_daily.csv`, `data/peak_daily_newarea.csv` | Core and new-area daily series |
| `data/peak_by_camera_*.csv` | Per camera per day |
| `data/counts/<date>.csv.gz` | Per-frame vehicle and Cybercab counts with boxes |
| `data/counts/box_<date>.csv.gz` | The same, from the original box (through 2026-11-09) |
| `data/DEDUPE.md` | Parked-car dedupe rule |

## History and continuity
The series through 2026-11-09 was produced on a separate box by the original pipeline.
This repository continues it with the same panels, window, detector weights, classifier
and dedupe rule. Two things differ and could shift levels slightly: the detection
libraries are whatever versions GitHub installs, and core frames are now shared with the
expanded panel instead of being fetched a few seconds apart. Re-running this code over
the box's raw detections reproduces all 12 days of the headline series exactly.

## Attribution
Camera snapshots: City of Austin. Service-area boundary and training crops: Robotaxi
Tracker (see `ATTRIBUTION.md`).
