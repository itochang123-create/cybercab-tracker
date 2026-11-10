#!/usr/bin/env python3
"""Weekday-only trailing moving averages for Austin Cybercab peak series.

weekday_5d_ma for date D = mean of per_1k_vehicles over the last 5 Mon–Fri
dates with data on or before D (if D is a weekday it is included; if D is a
weekend, use the last 5 weekdays up to D). Report n when fewer than 5 exist.

Also exposes change vs the previous non-overlapping weekday-5d window when
that prior window has a full 5 weekdays.

v2 (2026-11-10): `ma` is unchanged. Added next to it:
  - US holidays with weekend-like traffic (HOLIDAYS) are left out exactly like
    weekends, because thin traffic inflates the per-1,000-vehicles rate.
  - pooled: total Cybercabs / total vehicles over the same days, with a 95%
    Poisson interval. Unlike the mean of daily rates it has an error bar.
  - ma_ex_max / median: how much of the average rests on its single highest day.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


# Weekdays that behave like weekends on Austin roads. Extend each year.
HOLIDAYS = {
    "2026-11-26": "Thanksgiving",
    "2026-11-27": "Day after Thanksgiving",
    "2026-12-24": "Christmas Eve",
    "2026-12-25": "Christmas Day",
    "2027-01-01": "New Year's Day",
    "2027-01-18": "Martin Luther King Jr. Day",
    "2027-02-15": "Presidents' Day",
    "2027-05-31": "Memorial Day",
    "2027-07-05": "Independence Day (observed)",
    "2027-09-06": "Labor Day",
    "2027-11-25": "Thanksgiving",
    "2027-11-26": "Day after Thanksgiving",
    "2027-12-24": "Christmas Day (observed)",
    "2027-12-31": "New Year's Day (observed)",
}


def _is_weekday(date_str: str) -> bool:
    """Mon-Fri and not a listed holiday."""
    if date_str in HOLIDAYS:
        return False
    return datetime.strptime(date_str, "%Y-%m-%d").weekday() < 5  # Mon=0 .. Fri=4


def _poisson_ci(n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 3.689
    lo = n * (1 - 1 / (9 * n) - z / (3 * n ** 0.5)) ** 3
    m = n + 1
    hi = m * (1 - 1 / (9 * m) + z / (3 * m ** 0.5)) ** 3
    return max(0.0, lo), hi


def _int(row: dict, *keys: str) -> int:
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return int(float(v))
    return 0


def _metric(row: dict) -> float:
    v = float(row.get("per_1k_vehicles") or 0)
    if v:
        return v
    return float(row.get("per_1k_snapshots") or 0)


def weekday_rows_through(rows: list[dict], as_of: str) -> list[dict]:
    """Weekday rows with date <= as_of, sorted ascending, vehicles > 0 preferred."""
    out = []
    for r in rows:
        d = r.get("date") or ""
        if not d or d > as_of:
            continue
        if not _is_weekday(d):
            continue
        # skip empty thin rows with no vehicles (same filter as chart)
        if int(float(r.get("vehicles") or 0)) <= 0 and _metric(r) == 0:
            continue
        out.append(r)
    out.sort(key=lambda r: r["date"])
    return out


def weekday_5d_ma(rows: list[dict], as_of: str) -> dict[str, Any]:
    """Return ma, n, dates, values, and optional prior-window change."""
    wdays = weekday_rows_through(rows, as_of)
    window = wdays[-5:]
    vals = [_metric(r) for r in window]
    n = len(vals)
    ma = (sum(vals) / n) if n else None
    dates = [r["date"] for r in window]

    prior = None
    prior_ma = None
    prior_dates: list[str] = []
    change_pct = None
    if n == 5 and len(wdays) >= 10:
        prior = wdays[-10:-5]
        prior_vals = [_metric(r) for r in prior]
        prior_ma = sum(prior_vals) / 5
        prior_dates = [r["date"] for r in prior]
        if prior_ma:
            change_pct = 100.0 * (ma - prior_ma) / prior_ma

    # Pooled rate + interval over the same window.
    cabs = sum(_int(r, "cybercabs_deduped", "cybercabs") for r in window)
    veh = sum(_int(r, "vehicles") for r in window)
    pooled = pooled_lo = pooled_hi = None
    if veh:
        lo, hi = _poisson_ci(cabs)
        pooled, pooled_lo, pooled_hi = 1000.0 * cabs / veh, 1000.0 * lo / veh, 1000.0 * hi / veh

    # How much rests on the single highest day.
    ma_ex_max = median = max_date = max_value = None
    if n:
        srt = sorted(vals)
        median = srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2
        i_max = max(range(n), key=lambda i: vals[i])
        max_date, max_value = dates[i_max], vals[i_max]
        if n >= 2:
            ma_ex_max = (sum(vals) - max_value) / (n - 1)

    first = window[0]["date"] if window else as_of
    excluded_holidays = sorted(
        d for d in HOLIDAYS
        if first <= d <= as_of and datetime.strptime(d, "%Y-%m-%d").weekday() < 5
    )

    return {
        "ma": round(ma, 3) if ma is not None else None,
        "n": n,
        "dates": dates,
        "values": [round(v, 3) for v in vals],
        "pooled": round(pooled, 3) if pooled is not None else None,
        "pooled_lo95": round(pooled_lo, 3) if pooled_lo is not None else None,
        "pooled_hi95": round(pooled_hi, 3) if pooled_hi is not None else None,
        "pooled_cybercabs": cabs,
        "pooled_vehicles": veh,
        "ma_ex_max": round(ma_ex_max, 3) if ma_ex_max is not None else None,
        "median": round(median, 3) if median is not None else None,
        "max_date": max_date,
        "max_value": round(max_value, 3) if max_value is not None else None,
        "excluded_holidays": excluded_holidays,
        "prior_ma": round(prior_ma, 3) if prior_ma is not None else None,
        "prior_n": len(prior) if prior is not None else 0,
        "prior_dates": prior_dates,
        "change_pct": round(change_pct, 1) if change_pct is not None else None,
    }


def all_days_7d_avg(rows: list[dict], as_of: str) -> dict[str, Any]:
    """Calendar trailing 7 rows with date <= as_of (existing headline companion)."""
    usable = [r for r in rows if r.get("date") and r["date"] <= as_of]
    usable.sort(key=lambda r: r["date"])
    last7 = usable[-7:]
    vals = [_metric(r) for r in last7]
    n = len(vals)
    return {
        "ma": round(sum(vals) / n, 3) if n else None,
        "n": n,
        "dates": [r["date"] for r in last7],
    }


def series_weekday_5d(rows: list[dict]) -> list[tuple[str, float | None, int]]:
    """Per-date weekday_5d_ma for charting (one point per row date)."""
    out = []
    for r in sorted(rows, key=lambda x: x["date"]):
        info = weekday_5d_ma(rows, r["date"])
        out.append((r["date"], info["ma"], info["n"]))
    return out
