#!/usr/bin/env python3
"""peak_monitor.png — HEADLINE = expanded 250-cam weekday-only 5-day MA
(data/peak_daily_expanded.csv, from 2026-10-29); all-days 7-day avg kept as
secondary. Core 81-cam series (data/peak_daily.csv) overlaid lighter.
Metric: Cybercabs per 1,000 vehicles, parked-car deduped, AM 07:00–09:00 America/Chicago.
Weekend daily points use hollow markers.
"""
from __future__ import annotations
import csv
import os
import sys
from datetime import datetime
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

ROOT = Path(os.environ.get("CYBERCAB_ROOT", "/workspace/cybercab"))
sys.path.insert(0, str(ROOT))
from weekday_ma import weekday_5d_ma  # noqa: E402

_dd = os.environ.get("CYBERCAB_DATA_DIR")
DATA = (ROOT / _dd) if _dd else ROOT / "data"
EXP = DATA / "peak_daily_expanded.csv"
CORE = DATA / "peak_daily.csv"
# a non-default data dir (smoke test) keeps its chart next to its data
OUT = (DATA.parent / "peak_monitor.png") if _dd and _dd != "data" else ROOT / "charts" / "peak_monitor.png"


def load_rows(p: Path):
    if not p.exists():
        return []
    return [r for r in csv.DictReader(p.open()) if int(float(r.get("vehicles") or 0)) > 0]


def series(rows: list[dict]):
    d = [datetime.strptime(r["date"], "%Y-%m-%d") for r in rows]
    v = [float(r["per_1k_vehicles"]) for r in rows]
    # calendar 7-day trailing window (all days)
    roll7 = []
    for i, di in enumerate(d):
        w = [v[j] for j in range(len(d)) if 0 <= (di - d[j]).days <= 6]
        roll7.append(sum(w) / len(w))
    # weekday-only 5-day MA at each date
    wd5 = []
    for r in rows:
        info = weekday_5d_ma(rows, r["date"])
        wd5.append(info["ma"])
    weekend = [dt.weekday() >= 5 for dt in d]
    return d, v, roll7, wd5, weekend


def main():
    erows = load_rows(EXP)
    crows = load_rows(CORE)
    ed, ev, er7, ewd5, ewe = series(erows) if erows else ([], [], [], [], [])
    cd, cv, cr7, cwd5, cwe = series(crows) if crows else ([], [], [], [], [])

    fig, ax = plt.subplots(figsize=(10, 5), dpi=140)

    if cd:
        # core daily: solid weekdays, hollow weekends
        for i, (di, vi, we) in enumerate(zip(cd, cv, cwe)):
            if we:
                ax.plot(di, vi, "o", color="#9ecae1", markersize=4, markerfacecolor="none",
                        markeredgewidth=1.2, alpha=0.7, zorder=3,
                        label="Core 81 — weekend" if i == next(j for j, w in enumerate(cwe) if w) else None)
            else:
                ax.plot(di, vi, "o", color="#9ecae1", markersize=3.5, alpha=0.6, zorder=3,
                        label="Core 81 — weekday daily" if i == next((j for j, w in enumerate(cwe) if not w), 0) else None)
        ax.plot(cd, cv, "-", color="#9ecae1", alpha=0.45, linewidth=1, zorder=2, label="_nolegend_")
        ax.plot(cd, cr7, "--", color="#fdbe85", alpha=0.65, linewidth=1.2, label="Core 81 — 7-day avg (all days)")
        if any(x is not None for x in cwd5):
            ax.plot(cd, cwd5, ":", color="#a1d99b", alpha=0.8, linewidth=1.4,
                    label="Core 81 — weekday 5d MA")

    if ed:
        # expanded daily line + distinct weekend hollow markers
        ax.plot(ed, ev, "-", color="#1f77b4", linewidth=1.6, alpha=0.85, zorder=4, label="_nolegend_")
        labeled_wd = labeled_we = False
        for di, vi, we in zip(ed, ev, ewe):
            if we:
                ax.plot(di, vi, "o", color="#1f77b4", markersize=7, markerfacecolor="none",
                        markeredgewidth=1.6, zorder=5,
                        label="Expanded 250 — weekend daily" if not labeled_we else None)
                labeled_we = True
            else:
                ax.plot(di, vi, "o", color="#1f77b4", markersize=6, zorder=5,
                        label="Expanded 250 — weekday daily" if not labeled_wd else None)
                labeled_wd = True
        # all-days 7d secondary
        ax.plot(ed, er7, "--", color="#e6550d", linewidth=1.6, alpha=0.85,
                label="Expanded 250 — 7-day avg (all days)")
        # weekday-5d HEADLINE
        ax.plot(ed, ewd5, "-", color="#d62728", linewidth=2.8, zorder=6,
                label="Expanded 250 — weekday 5d MA (HEADLINE)")
        if ewd5 and ewd5[-1] is not None:
            ax.annotate(f"{ewd5[-1]:.3f}", (ed[-1], ewd5[-1]), textcoords="offset points",
                        xytext=(6, 4), color="#d62728", fontweight="bold")
        if er7:
            ax.annotate(f"{er7[-1]:.3f}", (ed[-1], er7[-1]), textcoords="offset points",
                        xytext=(6, -12), color="#e6550d", fontsize=8, alpha=0.9)

    # Service-area expansion: after this date the panels cover only part of the area.
    exp_day = datetime(2026, 11, 9)
    if ed and ed[0] <= exp_day <= ed[-1]:
        ax.axvline(exp_day, color="#636363", linestyle=":", linewidth=1.2, zorder=1)
        ax.annotate("service area expanded", (exp_day, ax.get_ylim()[0]), textcoords="offset points",
                    xytext=(-4, 4), ha="right", va="bottom", fontsize=7.5, color="#636363")

    ax.set_title("Austin Cybercab AM monitor — headline: 250-cam weekday 5-day MA\n"
                 "(07:00–09:00 America/Chicago, parked-car deduped; weekends hollow; "
                 "all-days 7d secondary)")
    ax.set_ylabel("Cybercabs per 1,000 vehicles")
    ax.set_xlabel("Austin date")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best", fontsize=7.5)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    fig.autofmt_xdate()
    fig.tight_layout()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT)
    plt.close()
    print("wrote", OUT, "expanded n=", len(ed), "core n=", len(cd))
    if ed and ewd5 and ewd5[-1] is not None:
        info = weekday_5d_ma(erows, erows[-1]["date"])
        print(f"expanded weekday_5d_ma={info['ma']:.3f} n={info['n']} dates={info['dates']}")


if __name__ == "__main__":
    main()
