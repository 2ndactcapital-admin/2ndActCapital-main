"""gen_mkt04b_golden.py — golden rebase vectors for the chart's browser rebasing.

Writes apps/web/tests/market/golden_rebase.json from CANNED series, computed by
the PRODUCTION transforms (services/market_data/transforms.transform_column)
and the PRODUCTION stddev text (read_service.stddev_text, the series
endpoint's `stddev` field). No database, no network, no secrets.

apps/web/tests/market/rebase.test.mjs asserts the browser's rebase.mjs matches
every value here to within 5e-7 (the server's 6-decimal rounding), and the
same unavailable reasons, warnings and floating flags.

The sigma vectors divide by the QUANTIZED stddev — the number the browser
actually receives — so the comparison isolates the rebase arithmetic.

Run:   apps/api/venv/bin/python apps/api/scripts/gen_mkt04b_golden.py
Check: apps/api/venv/bin/python apps/api/scripts/gen_mkt04b_golden.py --check
       (exit 1 when the committed file differs from a fresh build)
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve()
API_DIR = HERE.parents[1]
REPO = HERE.parents[3]
sys.path.insert(0, str(API_DIR))

from services.market_data.read_service import stddev_text  # noqa: E402
from services.market_data.transforms import Series, decimal_text, transform_column  # noqa: E402

OUT = REPO / "apps" / "web" / "tests" / "market" / "golden_rebase.json"
MEASURES = ("index", "sigma")

MONTHLY = [
    ("2020-01-31", "100.5"), ("2020-02-29", "102.25"), ("2020-03-31", "98.125"),
    ("2020-04-30", "104.0"), ("2020-05-29", "110.75"), ("2020-06-30", "107.3"),
    ("2020-07-31", "4123.456789"), ("2020-08-31", "0.000123"),
]

# name, default_transform, points, anchor, why
CASES = [
    ("anchor_on_observation", "rebase_100", MONTHLY, "2020-03-31",
     "the anchor date is an observation date: v0 is that observation"),
    ("anchor_between_observations", "rebase_100", MONTHLY, "2020-04-15",
     "between two observations the as-of rule takes the earlier one"),
    ("anchor_before_first_observation", "rebase_100", MONTHLY, "2019-06-30",
     "before the series starts: v0 is the first observation and the series floats"),
    ("non_positive_anchor_negative", "rebase_100",
     [("2021-01-29", "-1.5"), ("2021-02-26", "0.75"), ("2021-03-31", "2.25"), ("2021-04-30", "-0.5")],
     "2021-01-29", "index needs v0 > 0; sigma does not"),
    ("non_positive_anchor_zero", "rebase_100",
     [("2021-01-29", "0"), ("2021-02-26", "0.75"), ("2021-03-31", "2.25")],
     "2021-02-01", "v0 = 0 (as of a later date) is non-positive too"),
    ("zero_variance", "rebase_100",
     [("2022-01-31", "5.00"), ("2022-02-28", "5.00"), ("2022-03-31", "5.00"), ("2022-04-29", "5.00")],
     "2022-02-28", "a constant series has stddev null: sigma is unavailable, index is flat 100"),
    ("single_observation", "rebase_100", [("2022-06-30", "12.5")], "2022-06-30",
     "fewer than two observations: stddev null (zero_variance for sigma)"),
    ("rate_like_level_series", "level",
     [("1999-12-31", "6.45"), ("2000-01-31", "6.66"), ("2000-02-29", "6.52"), ("2000-03-31", "6.26"),
      ("2000-04-28", "5.99"), ("2000-05-31", "6.44")],
     "2000-02-29", "a level series still indexes but carries rate_like_series_indexed"),
    ("large_values", "rebase_100",
     [("2007-10-31", "1549.38"), ("2008-10-31", "968.75"), ("2009-03-31", "797.87"), ("2015-12-31", "2043.94"),
      ("2020-02-28", "2954.22"), ("2026-09-30", "6688.4612")],
     "2009-03-09", "float rounding on four- and five-digit index values"),
]


def eval_dates(points: list[tuple[str, str]], anchor: str) -> list[str]:
    """Every observation date, the anchor, a date before the first, one
    between each pair, and one after the last."""
    ds = [date.fromisoformat(d) for d, _ in points]
    extra = {ds[0].replace(day=1) if ds[0].day > 1 else date(ds[0].year - 1, 12, 15), date.fromisoformat(anchor)}
    for a, b in zip(ds, ds[1:]):
        extra.add(date.fromordinal((a.toordinal() + b.toordinal()) // 2))
    extra.add(date.fromordinal(ds[-1].toordinal() + 10))
    return sorted({d.isoformat() for d in ds} | {d.isoformat() for d in extra})


def build() -> dict:
    cases = []
    for name, default_transform, raw_points, anchor, why in CASES:
        points = [(date.fromisoformat(d), Decimal(v)) for d, v in raw_points]
        values = [v for _, v in points]
        sd_full = statistics.stdev(values) if len(values) >= 2 else None  # numeric stddev_samp
        sd_field = stddev_text(sd_full)
        dates = eval_dates(raw_points, anchor)
        measures = {}
        for mode in MEASURES:
            col = transform_column(
                Series(points), [date.fromisoformat(d) for d in dates],
                mode=mode, default_transform=default_transform, anchor=date.fromisoformat(anchor),
                first_observation_date=points[0][0],
                sd=Decimal(sd_field) if sd_field is not None else None,
            )
            measures[mode] = {
                "floating": col.floating,
                "unavailable_reason": col.unavailable_reason,
                "warnings": list(col.warnings),
                "anchor_observation_date": col.anchor_date.isoformat() if col.anchor_date else None,
                "anchor_value": decimal_text(col.anchor_value),
                "values": {d: decimal_text(v) for d, v in zip(dates, col.values)},
            }
        cases.append({
            "name": name,
            "why": why,
            "default_transform": default_transform,
            "anchor": anchor,
            "series": {
                "series_key": f"golden.{name}",
                "first_observation_date": raw_points[0][0],
                "last_observation_date": raw_points[-1][0],
                "stddev": sd_field,
                "points": [[d, v] for d, v in raw_points],
            },
            "measures": measures,
        })
    return {
        "generator": "apps/api/scripts/gen_mkt04b_golden.py",
        "source": "services/market_data/transforms.transform_column + read_service.stddev_text",
        "tolerance": "5e-7",
        "cases": cases,
    }


def render(data: dict) -> str:
    return json.dumps(data, indent=1, sort_keys=False) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="compare with the committed file; write nothing")
    args = ap.parse_args()
    text = render(build())
    if args.check:
        same = OUT.is_file() and OUT.read_text(encoding="utf-8") == text
        print("golden_rebase.json is current" if same else "golden_rebase.json DIFFERS from a fresh build")
        return 0 if same else 1
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO)} ({len(json.loads(text)['cases'])} cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
