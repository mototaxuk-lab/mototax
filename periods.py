"""Shared period parsing for summaries and exports (Flow B/D/F/G).

resolve(text) turns a phrase like "last week", "this month", "this tax year",
or "1 June to 30 June" into a concrete date range with a friendly label.

UK tax year runs 6 April – 5 April.
"""
from __future__ import annotations

import datetime as dt
import re

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _week(ref: dt.date) -> tuple[dt.date, dt.date]:
    start = ref - dt.timedelta(days=ref.weekday())
    return start, start + dt.timedelta(days=6)


def _month(ref: dt.date) -> tuple[dt.date, dt.date]:
    start = ref.replace(day=1)
    nxt = (start.replace(year=start.year + 1, month=1) if start.month == 12
           else start.replace(month=start.month + 1))
    return start, nxt - dt.timedelta(days=1)


def _tax_year(ref: dt.date, offset: int = 0) -> tuple[dt.date, dt.date]:
    base_year = ref.year if (ref.month, ref.day) >= (4, 6) else ref.year - 1
    base_year += offset
    return dt.date(base_year, 4, 6), dt.date(base_year + 1, 4, 5)


def fmt(d: dt.date) -> str:
    return f"{d.day} {d:%b %Y}"


def _result(start: dt.date, end: dt.date, title: str, frequency: str) -> dict:
    return {"start": start, "end": end, "title": title, "frequency": frequency,
            "label": f"{fmt(start)} – {fmt(end)}"}


def _parse_date(token: str, default_year: int) -> dt.date | None:
    """Parse '1 June', '1 Jun 2026', '01/06/2026', '2026-06-01'."""
    token = token.strip()
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})$", token)
    if m:
        y, mo, d = map(int, m.groups())
        return _safe(y, mo, d)
    m = re.match(r"(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?$", token)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else default_year
        if y < 100:
            y += 2000
        return _safe(y, mo, d)
    m = re.match(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)\s*(\d{4})?$", token, re.I)
    if m:
        d = int(m.group(1))
        mo = _MONTHS.get(m.group(2).lower())
        y = int(m.group(3)) if m.group(3) else default_year
        if mo:
            return _safe(y, mo, d)
    m = re.match(r"([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\s*(\d{4})?$", token, re.I)
    if m:
        mo = _MONTHS.get(m.group(1).lower())
        d = int(m.group(2))
        y = int(m.group(3)) if m.group(3) else default_year
        if mo:
            return _safe(y, mo, d)
    return None


def _safe(y: int, mo: int, d: int) -> dt.date | None:
    try:
        return dt.date(y, mo, d)
    except ValueError:
        return None


def resolve(text: str, ref: dt.date | None = None) -> dict | None:
    """Return a period dict for a phrase, or None if no period is recognised."""
    ref = ref or dt.date.today()
    t = text.strip().lower()
    if not t:
        return None

    if t in ("this week", "current week", "week"):
        return _result(*_week(ref), "This week's", "weekly")
    if t in ("last week", "previous week", "past week"):
        return _result(*_week(ref - dt.timedelta(days=7)), "Last week's", "weekly")
    if t in ("this month", "current month", "month"):
        return _result(*_month(ref), "This month's", "monthly")
    if t in ("last month", "previous month", "past month"):
        first = ref.replace(day=1)
        return _result(*_month(first - dt.timedelta(days=1)), "Last month's", "monthly")
    if t in ("this tax year", "tax year", "current tax year", "this year", "year"):
        return _result(*_tax_year(ref), "This tax year's", "annual")
    if t in ("last tax year", "previous tax year", "last year"):
        return _result(*_tax_year(ref, -1), "Last tax year's", "annual")

    # Custom range: "1 June to 30 June", "1 Jun - 30 Jun 2026", "1/6 to 30/6".
    m = re.search(r"(.+?)\s*(?:to|-|–|until|through|thru)\s*(.+)", t)
    if m:
        s = _parse_date(m.group(1), ref.year)
        e = _parse_date(m.group(2), ref.year)
        if s and e and s <= e:
            return _result(s, e, "Selected period", "custom")
    return None
