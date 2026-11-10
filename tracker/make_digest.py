#!/usr/bin/env python3
"""Write the daily digest: LATEST.md, reports/digest_<date>.md and data/latest.json.

Everything is read from the daily CSVs the reports just wrote; nothing here
measures anything. data/latest.json is the machine-readable summary other
tools (the 8 AM update) read.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("CYBERCAB_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT))
from weekday_ma import weekday_5d_ma, all_days_7d_avg, _is_weekday, HOLIDAYS  # noqa: E402

_dd = os.environ.get("CYBERCAB_DATA_DIR")
DATA = (ROOT / _dd) if _dd else ROOT / "data"
_rd = os.environ.get("CYBERCAB_REPORTS_DIR")
REPORTS = (ROOT / _rd) if _rd else ROOT / "reports"
EXPANSION_DATE = "2026-11-09"


def load(name: str) -> list[dict]:
    p = DATA / name
    if not p.exists():
        return []
    with p.open() as f:
        return sorted(csv.DictReader(f), key=lambda r: r["date"])


def row_for(rows: list[dict], date: str) -> dict | None:
    return next((r for r in rows if r["date"] == date), None)


def f3(v) -> str:
    try:
        return f"{float(v):.3f}"
    except Exception:
        return "n/a"


def pct(new: float, old: float) -> str:
    return f"{100 * (new - old) / old:+.1f}%" if old else "n/a"


def offline_ids(mode_dir: Path, date: str) -> str:
    log = REPORTS / f"peak_run_expanded_{date}.log"
    if log.exists():
        for line in log.read_text().splitlines():
            if line.startswith("offline camera ids"):
                return line.split(":", 1)[1].strip()
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--mode", default="window")
    args = ap.parse_args()
    day = args.date
    exp, core, new = load("peak_daily_expanded.csv"), load("peak_daily.csv"), load("peak_daily_newarea.csv")
    e, c, n = row_for(exp, day), row_for(core, day), row_for(new, day)
    dow = datetime.strptime(day, "%Y-%m-%d").strftime("%a")
    weekday = _is_weekday(day)

    wd = weekday_5d_ma(exp, day)
    prev_days = [r["date"] for r in exp if r["date"] < day]
    wd_prev = weekday_5d_ma(exp, prev_days[-1]) if prev_days else {"ma": None}
    a7 = all_days_7d_avg(exp, day)
    status = "ok"
    problems = []
    if not e:
        status = "no_data"
        problems.append("No headline row for this date: sampling or detection did not complete.")
    else:
        if int(float(e.get("am_cycles") or 0)) < 18:
            status = "thin"
            problems.append(f"Thin window: only {e['am_cycles']} of 24 cycles.")
        if int(float(e.get("cams_ok") or 0)) < 200:
            status = "thin"
            problems.append(f"Only {e['cams_ok']} of 250 cameras reported.")

    L: list[str] = []
    out = L.append
    title = "Austin Cybercab tracker" + (" - SMOKE TEST (not part of the series)" if args.mode == "smoke" else "")
    out(f"# {title} - {day} ({dow})")
    out("")
    out("Cybercabs per 1,000 vehicles seen on City of Austin traffic cameras, 07:00-09:00 "
        "America/Chicago, parked cars counted once. Relative growth only: not fleet size, not miles.")
    out("")
    for p in problems:
        out(f"> **Data problem:** {p}")
    if problems:
        out("")

    out("## Lead number")
    out("")
    if wd["ma"] is not None:
        chg = ""
        if wd_prev.get("ma") and wd_prev["ma"] != wd["ma"]:
            chg = f", {pct(wd['ma'], wd_prev['ma'])} from {wd_prev['ma']:.3f}"
        elif wd_prev.get("ma"):
            chg = ", unchanged"
        out(f"**Weekday 5-day average: {wd['ma']:.3f}**{chg} "
            f"(250-camera panel; {', '.join(d[5:] for d in wd['dates'])}"
            + ("" if wd["n"] == 5 else f"; only {wd['n']} weekdays so far") + ").")
        out("")
        if wd.get("pooled") is not None:
            out(f"- Pooled over the same days: {wd['pooled']:.3f} (95% interval {wd['pooled_lo95']:.3f}-{wd['pooled_hi95']:.3f}, "
                f"{wd['pooled_cybercabs']} sightings).")
        if wd.get("ma_ex_max") is not None and wd["n"] >= 3:
            out(f"- Without its highest day ({wd['max_date'][5:]}, {wd['max_value']:.3f}): {wd['ma_ex_max']:.3f}. Median day: {wd['median']:.3f}.")
        if wd.get("change_pct") is not None:
            out(f"- Against the five weekdays before those: {wd['change_pct']:+.1f}% (was {wd['prior_ma']:.3f}).")
        if a7["ma"] is not None:
            out(f"- All-days 7-day average (secondary): {a7['ma']:.3f}.")
        if not weekday:
            why = HOLIDAYS.get(day)
            label = f"a holiday ({why})" if why else "a weekend day"
            out(f"- {day} is {label}: reported below, left out of the lead average because thin traffic inflates the rate.")
    else:
        out("No weekday data yet.")
    out("")

    out("## This morning")
    out("")
    out("| Panel | Per 1,000 vehicles | 95% interval | Sightings | Vehicles | Cameras with a sighting | Cameras reporting | Cycles |")
    out("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")

    def line(label: str, r: dict | None, size: int) -> None:
        if not r:
            out(f"| {label} | no data | | | | | | |")
            return
        ci = f"{f3(r.get('rate_lo95'))}-{f3(r.get('rate_hi95'))}" if r.get("rate_lo95") not in (None, "") else ""
        out(f"| {label} | {f3(r['per_1k_vehicles'])} | {ci} | "
            f"{r['cybercabs_deduped']} | {int(float(r['vehicles'])):,} | {r.get('cams_with_cc') or 'n/a'} | "
            f"{r['cams_ok']}/{size} | {r['am_cycles']} |")

    line("Expanded 250 (headline)", e, 250)
    line("Core 81 (legacy)", c, 81)
    line("New area 98 (context)", n, 98)
    out("")
    if e:
        prev_any = [r for r in exp if r["date"] < day and float(r.get("vehicles") or 0) > 0]
        bits = []
        if prev_any:
            bits.append(f"{pct(float(e['per_1k_vehicles']), float(prev_any[-1]['per_1k_vehicles']))} vs {prev_any[-1]['date'][5:]}")
        if weekday:
            pw = [r for r in prev_any if _is_weekday(r["date"])]
            if pw and (not prev_any or pw[-1]["date"] != prev_any[-1]["date"]):
                bits.append(f"{pct(float(e['per_1k_vehicles']), float(pw[-1]['per_1k_vehicles']))} vs the previous weekday ({pw[-1]['date'][5:]})")
        if bits:
            out("Headline panel day change: " + "; ".join(bits) + ".")
        off = offline_ids(REPORTS, day)
        if off:
            out(f"Offline headline cameras: {off}.")
        out("")
    if day >= EXPANSION_DATE:
        out("_Service area: Tesla expanded the Austin service area on 2026-11-09. The headline and core panels "
            "cover the original area only; the new-area panel is every City camera outside it and is never "
            "blended into the headline._")
        out("")

    extras = REPORTS / f"extras_{day}.md"
    if extras.exists():
        body = extras.read_text().split("\n", 1)[1].strip()
        out("## What moved")
        out("")
        out(body.replace("\n## ", "\n### "))
        out("")
    out("## Chart")
    out("")
    out("![Daily rate and weekday 5-day average](charts/peak_monitor.png)")
    out("")
    out(f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by the daily GitHub job._")
    text = "\n".join(L) + "\n"

    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"digest_{day}.md").write_text(text)
    latest = {
        "date": day, "weekday": weekday, "mode": args.mode, "status": status, "problems": problems,
        "lead_weekday_5d_avg": wd["ma"], "lead_previous": wd_prev.get("ma"), "lead_dates": wd["dates"],
        "lead_pooled": wd.get("pooled"), "lead_pooled_lo95": wd.get("pooled_lo95"), "lead_pooled_hi95": wd.get("pooled_hi95"),
        "lead_without_highest_day": wd.get("ma_ex_max"), "all_days_7d_avg": a7["ma"],
        "expanded": e, "core": c, "newarea": n,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if args.mode == "smoke":
        (DATA / "latest.json").write_text(json.dumps(latest, indent=1))
        (DATA.parent / "LATEST.md").write_text(text.replace("(charts/peak_monitor.png)", "(peak_monitor.png)"))
    else:
        (ROOT / "data" / "latest.json").write_text(json.dumps(latest, indent=1))
        (ROOT / "LATEST.md").write_text(text)
    print(text)
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a") as f:
            f.write(text.split("## Chart")[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
