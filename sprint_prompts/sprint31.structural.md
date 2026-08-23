# SPRINT 31 — SSVI Volatility Surface Engine + Admin Viewer

**Tier:** `.structural` (hold for manual smoke-test before merge)
**Tasks:** 3
**Depends on:** none
**Blocks:** S35 (pricing engine), S38 (lifecycle monitoring)

---

## PART 1 — PREMISE VERIFICATION (run before generating any code)

Do not trust anything below until it is confirmed against the live system.

```sql
-- 1. Confirm no prior pricing/surface tables exist
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
  AND (table_name ILIKE '%surface%'
       OR table_name ILIKE '%vol%'
       OR table_name ILIKE '%pricing%');

-- 2. Confirm the org_settings key namespace is free
SELECT key FROM org_settings WHERE key LIKE 'pricing.%';

-- 3. Confirm super_admin role name and permission-set shape
SELECT * FROM roles WHERE name ILIKE '%super%';
```

```bash
# 4. Confirm existing admin route registration prefix
grep -rn "api/v1/admin" backend/app/main.py backend/app/api/ | head -20

# 5. Confirm current Python deps — is scipy already present?
cat backend/requirements.txt

# 6. Confirm Render instance memory allocation for the API service
#    (Render dashboard -> service -> Settings -> Instance Type)
```

**STOP and report if:** any surface/pricing table already exists, scipy is already
pinned at a conflicting version, or the API service instance is below 1GB.

Refresh and commit the schema snapshot (`scripts/refresh_schema.py`) before
writing code.

**File location:** `backend_reference/ssvi_surface.py` already exists in the repo
(694 lines, verified 8/8 self-test, includes a 2024 patch tightening the
discount-factor sanity bound and adding exception handling in `main()`). This is
the file to copy in for Task 1 — do NOT rewrite it from scratch, and do NOT modify
the math. Confirm it parses (`python3 -c "import ast; ast.parse(open('backend_reference/ssvi_surface.py').read())"`)
before using it.

---

## PART 2 — TASKS

### Task 1 — Land the pricing module and endpoint

**Files**
- `backend/app/services/pricing/__init__.py`
- `backend/app/services/pricing/ssvi_surface.py` — copy from `backend_reference/ssvi_surface.py` verbatim, do not modify the math
- `backend/app/services/pricing/memory_guard.py` — new, see below
- `backend/app/api/v1/admin/pricing.py` — new router
- `backend/requirements.txt` — add `scipy>=1.10`, `yfinance>=0.2`

**Endpoint**

```
POST /api/v1/admin/pricing/surface
body: { "ticker": "^SPX" }
auth: super_admin only
```

Note the `/api/v1/admin/` prefix — admin routes are registered under it, not bare
`/api/v1/`. This has bitten us before.

`org_id` is derived server-side from the session. Never read it from the body.

**Response** — the module already emits this shape via `dataclasses.asdict(SurfaceFit)`.
Return it unchanged plus the raw per-slice market points needed for charting:

```json
{
  "status": "ok",
  "fit": { "rho": -0.71, "eta": 1.14, "gamma": 0.38,
           "rmse_iv_pooled": 0.0089, "rmse_iv_peak_slice": 0.0141,
           "n_points": 412, "n_slices": 9,
           "max_listed_maturity": 1.94, "theta_adjusted": false,
           "ticker": "^SPX", "as_of": "...", "version": "1.0.0",
           "per_slice": [ ... ] },
  "market_points": [ { "T": 0.12, "k": -0.043, "iv": 0.171 }, ... ]
}
```

**Error contract** — every failure returns a typed reason, never a bare 500:

| Condition | HTTP | `status` |
|---|---|---|
| `InsufficientDataError` | 422 | `insufficient_data` |
| `SurfaceQualityError` | 422 | `quality_gate_failed` |
| `SurfaceArbitrageError` | 422 | `arbitrage_violation` |
| memory headroom too low | 503 | `insufficient_memory` |
| `MemoryError` during run | 503 | `out_of_memory` |
| upstream fetch failure | 502 | `data_provider_error` |

Every 4xx/5xx carries the exception message in `detail` so the UI can show it.
The module's `main()` now demonstrates the correct exception-to-message mapping
for these three error types — mirror that pattern in the endpoint handler.

**Memory guard — required**

Render kills an OOM container; the process cannot catch it after the fact. The
guard must be preemptive.

`memory_guard.py` must:

1. Read the cgroup v2 limit (`/sys/fs/cgroup/memory.max`, `memory.current`),
   falling back to cgroup v1 paths, falling back to `psutil` if neither is
   readable. Never crash if the paths are absent — return `None` and proceed.
2. Expose `assert_headroom(required_mb: int)` — raises `InsufficientMemoryError`
   before any heavy import or fetch if free memory is below the requirement.
   Set the requirement at 400MB.
3. Call `resource.setrlimit(RLIMIT_AS, ...)` at ~85% of the container limit so an
   overrun raises a catchable `MemoryError` instead of being SIGKILLed.
4. Wrap the calibration call in `try/except MemoryError` and return 503.

Additional containment inside the endpoint:

- Import `yfinance` and `pandas` **inside the request handler**, not at module
  top, so the base service does not carry them at idle. The module already does
  this in `build_slices_from_yahoo`.
- **Never import matplotlib on the server.** The module only imports it under
  `--plot`; keep it that way.
- Cap processed expiries at 25 (`SurfaceConfig` field, not a literal). Report the
  cap in the response when it binds.
- `del` the chain DataFrames and call `gc.collect()` after each expiry — only the
  extracted arrays are retained.
- Log peak RSS at completion so we can size the instance from data rather than
  guesswork.

**Timeout** — synchronous, 15–40s expected. Set the request timeout to 90s and
return `504` with `status: "timeout"` past that. No background jobs this sprint.

---

### Task 2 — Admin route and results UI

**Files**
- `frontend/app/admin/pricing/surface/page.tsx`
- nav entry in the admin sidebar, gated on `super_admin`

**Layout** — Signature palette from tenant config, light theme. No dark mode.
Cream/white backgrounds, navy headings, gold accents. Base font 17px.

**Input card** (deliberately minimal — the module reads the market, there is
little to parameterize):
- Ticker select: `^SPX`, `^XSP`
- "Calibrate" button
- Loading state that names the phase: fetching chains → calibrating → validating.
  A 40s spinner with no text reads as a hang.

**Results — headline card**

| Field | Display |
|---|---|
| ρ | 4dp, labelled "skew (well identified)" |
| η, γ | 4dp, labelled "weakly identified — expect drift" |
| Pooled IV RMSE | vol points, 2dp, green under 1.0 / amber 1.0–1.5 / red above |
| Peak slice IV RMSE | vol points, 2dp |
| Quotes / maturities | counts |
| Longest listed tenor | years, with an explicit "beyond this is extrapolation" note |
| θ adjusted | warning badge when true |

**Per-slice table** — DataGrid component (TanStack Table + @dnd-kit, per the
Grid UX sprints). Columns: T, θ_atm, quotes, RMSE IV, max abs IV error, forward,
discount factor.

**Failure states** are first-class, not toasts. Render the typed `status` with
the message and, for `quality_gate_failed`, show which threshold was breached and
by how much. A rejected fit is a legitimate outcome, not an error to hide.

---

### Task 3 — Smile chart

One chart per maturity, or a maturity selector with a single chart.

- x: log-moneyness k = ln(K/F)
- y: implied vol
- Market quotes as points, model curve as a line
- Mark the extrapolation boundary at `max_listed_maturity`
- Signature palette; navy line, gold points, cream background

The model curve is computed client-side from ρ/η/γ/θ_atm using the same SSVI
formula. Do not round-trip to the server for chart points.

---

## PART 3 — VERIFY SCRIPT

`scripts/verify_sprint_31.py` — checks run, pass/fail reported, nothing else. No
interactive prompts, no note-entry, no save step.

Verify **effects, not exit codes**.

| # | Check |
|---|---|
| 1 | `ssvi_surface.py` present at the expected path and `ast.parse` succeeds |
| 2 | `python -m app.services.pricing.ssvi_surface --self-test` exits 0 with 8/8 |
| 3 | `--synthetic` produces a fit with pooled RMSE < 1.5 vol points |
| 4 | `memory_guard.assert_headroom(999999)` raises `InsufficientMemoryError` |
| 5 | Memory guard returns `None` (not raising) when cgroup paths are absent |
| 6 | Route registered under `/api/v1/admin/pricing/surface`, not bare `/api/v1/` |
| 7 | Endpoint returns 403 for a non-`super_admin` session |
| 8 | `org_id` is not read from the request body anywhere in `pricing.py` (grep) |
| 9 | matplotlib is not imported at module scope in any server-side file (grep) |
| 10 | Frontend route file exists and nav entry is gated on `super_admin` |
| 11 | No hardcoded hex colors in the new frontend files — palette from tenant config (grep) |
| 12 | Each typed error status is reachable — assert the mapping exists in the handler |

---

## PART 4 — MERGE AND ROLLBACK

**Tier `.structural`** — hold for manual smoke-test. Do not auto-merge.

**Smoke test after deploy** (production only; use Render's own logs for backend
tracebacks):
1. Hit the endpoint during US market hours with `^SPX`. Record pooled RMSE.
2. Hit it outside market hours. Confirm it either succeeds on stale quotes or
   fails with a typed `insufficient_data`, not a 500.
3. Check Render logs for peak RSS. If above 70% of the instance limit, stop and
   resize before merging.
4. Confirm a non-`super_admin` account cannot see the nav item.

**Rollback:** revert the branch. No migration this sprint, so rollback is clean.

**Do not merge if:** peak RSS exceeds 70% of the instance limit, or the endpoint
returns any bare 500.

---

## NOTES FOR THE IMPLEMENTER

- Do not modify the math in `ssvi_surface.py`. Every constant was calibrated by
  measurement across 40 noise seeds. The full rationale is in
  `Hollisworks_Structured_Investments_Module.docx` §5.2.
- No persistence this sprint. `vol_surface_fits`, R2 quote snapshots, and the
  nightly EventBridge trigger are deliberately deferred — this sprint answers
  "does a free surface work on real SPX," and that question needs no storage.
- The module never falls back to SPY. SPY is American-exercise and would silently
  bias the derived forward. If `^SPX` and `^XSP` both fail, that is a legitimate
  422.
- `Decimal` is not required here — this is surface math, not money. `Decimal`
  applies at the pricing layer above.

## SUCCESS CRITERION

One number: **pooled IV RMSE against live SPX.** Under ~1.0 vol point means SSVI's
three parameters are adequate and the ORATS subscription moves to phase three.
Above 1.5 means we need eSSVI or per-slice SVI with calendar constraints — not a
looser tolerance.
