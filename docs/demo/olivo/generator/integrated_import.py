"""PORTENTRY1 Task 6 — generate data/import/integrated_positions.csv from data/holdings.csv for the eight INTEGRATED
(Altruist) accounts, in the one-time import format (services/integrated_import.py; docs/demo/olivo/README.md).

    python3 docs/demo/olivo/generator/integrated_import.py

Accounts are identified by their MASK (accounts.csv account_mask, e.g. ****2201) — never a full number. Values and
cost bases are copied exactly (text, no float arithmetic). Nothing is loaded: an operator uploads the file on the
Super Admin screen and a second person approves it.
"""

from __future__ import annotations

import csv
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE.parent / "data"
OUT = DATA / "import" / "integrated_positions.csv"
INTEGRATED = ("A7", "A9", "A12", "A14", "A15", "A20", "A24", "A29")
AS_OF = "2026-11-04"   # the dataset's as-of (olivo_family.json)
#: holdings.csv asset_class → the platform taxonomy key (config asset_taxonomy, super / major class)
TAXONOMY = {"Public equity": "taxonomy_sc_1", "Fixed income": "taxonomy_sc_2", "Cash": "taxonomy_mc_8_1"}
COLUMNS = ("account_mask", "as_of_date", "name", "ticker", "cusip", "asset_class", "sub_class", "taxonomy_key",
           "quantity", "price", "market_value", "cost_basis")


def main() -> int:
    accounts = {r["id"]: r for r in csv.DictReader((DATA / "accounts.csv").open(encoding="utf-8"))}
    for a in INTEGRATED:
        if accounts[a]["custody"] != "integrated":
            raise SystemExit(f"{a} is not an integrated account in accounts.csv")
    rows = []
    for h in csv.DictReader((DATA / "holdings.csv").open(encoding="utf-8")):
        if h["account_id"] not in INTEGRATED:
            continue
        rows.append({"account_mask": accounts[h["account_id"]]["account_mask"], "as_of_date": AS_OF,
                     "name": h["security"], "ticker": h["ticker"], "cusip": "", "asset_class": h["asset_class"],
                     "sub_class": h["sub_class"], "taxonomy_key": TAXONOMY.get(h["asset_class"], ""),
                     "quantity": h["quantity"], "price": h["price"], "market_value": h["value_usd"],
                     "cost_basis": h["cost_basis_usd"]})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"{OUT.relative_to(HERE.parents[3])}: {len(rows)} lines, {len({r['account_mask'] for r in rows})} accounts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
