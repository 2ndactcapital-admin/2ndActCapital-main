# CASH03 recorded fixture payloads

Hand-built to the documented shapes (docs/cash/CASH03_NOTES.md §3 and §4). No live payload was recorded: the sprint
had no network. Every identifier is a FIXTURE value that matches no real row (the verify asserts this):

- CUSIPs `99C030xxx` (valid check digits);
- SEC series `S99003000n` and classes `C99003000n`;
- CIKs `990030n`.

Each file's content is distinct.

| File | What it is | Expected parse |
|---|---|---|
| `tfd_auctions_p1.json` | auctions_query page 1 of 2 (5 rows; one with a bad check digit) | 4 auctions; 1 row rejected (CUSIP) |
| `tfd_auctions_p2.json` | page 2 of 2 (a TIPS note, a reopening of the 2-year note) | 2 auctions |
| `tfd_bad_not_json.txt` | an HTML error page | PayloadRejected: not JSON |
| `tfd_bad_partial.json` | meta.count 3, data 2 | PayloadRejected: partial page |
| `tfd_bad_no_data.json` | an error object | PayloadRejected: no 'data' list |
| `nmfp_treasury.xml` | N-MFP3, Treasury category, a dated 7-day yield list | S990030001 / C990030001, 4.50 % as of 2026-10-31, WAM 32, WAL 88 |
| `nmfp_government.xml` | N-MFP3, Government, two classes, bare values | C990030002 4.50 %, C990030003 4.60 % (as of the report date 2026-10-31) |
| `nmfp_muni.xml` | N-MFP2, Other Tax Exempt | C990030004 3.10 % |
| `nmfp_bad_no_yield.xml` | a class without a 7-day yield | FilingRejected: no 7-day net yield |
| `nmfp_bad_percent.xml` | a yield written as a percent (5.12) | FilingRejected: not a fraction |
| `nmfp_bad_doctype.xml` | declares a DOCTYPE / ENTITY | FilingRejected: refused, never expanded |
| `edgar_master_idx.txt` | an EDGAR master index excerpt: 3 N-MFP3 + 1 N-MFP2 + a 424B2 line | 4 N-MFP filings |
