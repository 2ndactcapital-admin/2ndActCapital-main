# Template Study — inventory of what structured-note filings contain

**Status: not yet run.** This file is (re)generated from a stored inventory run by

    python3 apps/api/scripts/run_edgar_inventory.py --write-doc <inventory-run-id>

and is written automatically at the end of a real run of

    python3 apps/api/scripts/run_edgar_inventory.py --cohort <template-study-cohort-id> --spend-cap <USD>

Steps (edgarcohorts.structural):

1. EDGAR Pipeline page, Cohorts tab: "Build template study". This takes every issuer with
   include status 'yes', about 8 filings per issuer per era (2019-2021, 2022-2023, 2024-2026),
   oversampled because the document kind is only known after fetch.
2. Fetch that cohort ("Fetch this cohort").
3. Register a non-Claude chat model in `platform_model_catalog` that the LiteLLM proxy serves
   under the same name. Today none is available, so the inventory pass reports BLOCKED.
4. Dry run first (`--dry-run`). It shows the documents chosen, terms-page token counts and an
   estimated cost, and calls no model. Then run with a spending cap.

The output has three parts: the documents read, the concepts (synonymous labels, issuers,
frequency, example values, existing or proposed field, misleading-label flags), and the
per-issuer label dictionary.
