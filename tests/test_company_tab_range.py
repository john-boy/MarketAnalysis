"""Tests for Company-tab chart range helpers.

Covers the pure ``CompanyTab._x_window_bounds`` slicing logic used by
the quick-range toolbar and the 1Y default window on symbol load.
Does not spin up a QApplication — the helper is a staticmethod and the
test only imports the class.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from market_analysis.app.tabs.company_tab import CompanyTab


@dataclass
class _Q:
    date: datetime


def _series(n: int) -> list[_Q]:
    base = datetime(2024, 1, 1)
    return [_Q(date=base + timedelta(days=i)) for i in range(n)]


def test_x_window_bounds_none_when_no_quotes():
    assert CompanyTab._x_window_bounds([], 30) is None


def test_x_window_bounds_none_when_nbars_none():
    assert CompanyTab._x_window_bounds(_series(100), None) is None


def test_x_window_bounds_none_when_window_covers_all_bars():
    qs = _series(50)
    # Requesting more bars than available → full range.
    assert CompanyTab._x_window_bounds(qs, 200) is None
    # Equal to length also → full range.
    assert CompanyTab._x_window_bounds(qs, 50) is None


def test_x_window_bounds_returns_last_n_bars():
    qs = _series(500)
    bounds = CompanyTab._x_window_bounds(qs, 100)
    assert bounds is not None
    start, end = bounds
    assert start == qs[-100].date.timestamp()
    assert end == qs[-1].date.timestamp()
    assert end > start


def test_x_window_bounds_rejects_nonpositive():
    qs = _series(50)
    assert CompanyTab._x_window_bounds(qs, 0) is None
    assert CompanyTab._x_window_bounds(qs, -5) is None
