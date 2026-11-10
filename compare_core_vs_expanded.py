#!/usr/bin/env python3
"""Compare core 81 vs expanded panel peak_daily CSVs for A/B dates."""
from __future__ import annotations
import argparse
import csv
import os
from pathlib import Path

ROOT = Path(os.environ.get("CYBERCAB_ROOT", "/workspace/cybercab"))
CORE = ROOT / "data" / "peak_daily.csv"
EXP = ROOT / "data" / "peak_daily_expanded.csv"


def load(path: Path):
    if not path.exists():
        return {}
    with path.open() as f:
        return {r["date"]: r for r in csv.DictReader(f)}


def fmt(r, key, default="n/a"):
    if not r:
        return default
    return r.get(key, default)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", action="append", help="Austin date(s); default: latest headline date")
    args = ap.parse_args()
    if args.date:
        dates = args.date
    else:  # default: the latest date present in the headline CSV
        dates = sorted(load(EXP))[-1:] or ["2026-10-29", "2026-10-30"]
    core = load(CORE)
    exp = load(EXP)
    print("=== Core 81 vs Expanded 250 ===")
    for d in dates:
        c, e = core.get(d), exp.get(d)
        print(f"\n{d}")
        if not c and not e:
            print("  (no rows yet)")
            continue
        for label, r in (("core81", c), ("expanded", e)):
            if not r:
                print(f"  {label}: missing")
                continue
            print(
                f"  {label}: per_1k={r.get('per_1k_vehicles')} "
                f"cc_deduped={r.get('cybercabs_deduped')} raw={r.get('cybercabs_raw')} "
                f"veh={r.get('vehicles')} snaps={r.get('snapshots')} "
                f"cams_ok={r.get('cams_ok')}/{int(r.get('cams_ok') or 0)+int(r.get('cams_offline') or 0)} "
                f"cycles={r.get('am_cycles')} notes={r.get('notes')}"
            )
        if c and e:
            try:
                cv = float(c["per_1k_vehicles"] or 0)
                ev = float(e["per_1k_vehicles"] or 0)
                if cv:
                    print(f"  delta expanded vs core: {100*(ev-cv)/cv:+.1f}% per_1k "
                          f"({ev:.3f} vs {cv:.3f})")
                else:
                    print(f"  delta: core per_1k=0; expanded={ev:.3f}")
            except Exception as ex:
                print("  delta: n/a", ex)
        # Weekday-only 5d MA alongside existing 7d-style daily compare
        try:
            import sys
            sys.path.insert(0, str(ROOT))
            from weekday_ma import weekday_5d_ma, all_days_7d_avg
            crow = list(core.values())
            erow = list(exp.values())
            for label, rows in (("core81", crow), ("expanded", erow)):
                wd = weekday_5d_ma(rows, d)
                a7 = all_days_7d_avg(rows, d)
                extra = ""
                if wd.get("change_pct") is not None:
                    extra = f"  vs prior wd5 {wd['prior_ma']:.3f} ({wd['change_pct']:+.1f}%)"
                print(f"  {label} weekday_5d_ma n={wd['n']}: {wd['ma']} "
                      f"dates={','.join(wd['dates'])}{extra}")
                print(f"  {label} 7d_avg n={a7['n']}: {a7['ma']}")
        except Exception as ex:
            print("  weekday_5d: n/a", ex)


if __name__ == "__main__":
    main()
