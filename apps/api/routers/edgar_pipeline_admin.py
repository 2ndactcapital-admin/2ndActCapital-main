"""EDGAR pipeline monitoring — SUPER ADMIN ONLY, not org-scoped.

    GET  /admin/edgar/filings          the manifest: server-side filter, sort, page
    GET  /admin/edgar/progress         counts by status per quarter + recent runs
    GET  /admin/edgar/issuers          issuer table + unlisted 424B2 filers
    PUT  /admin/edgar/issuers/{cik}    edit include_status / credit_entity / notes
    POST /admin/edgar/issuers          add an issuer (e.g. promote an unlisted filer)
    POST /admin/edgar/runs             "Run default policy now (newest first)": the nightly job
                                       with a fetch cap — everything TARGETED is a cohort run

  Cohorts (edgarcohorts) — named, FROZEN lists of filings:
    GET  /admin/edgar/cohorts                          list + the builder's vocabularies
    POST /admin/edgar/cohorts/preview                  what a definition would freeze (nothing saved)
    POST /admin/edgar/cohorts                          save (frozen)
    POST /admin/edgar/cohorts/presets/template-study/preview | /admin/edgar/cohorts/presets/template-study
    GET  /admin/edgar/cohorts/{id}                     definition, counts by status, strata, runs, inventory runs
    GET  /admin/edgar/cohorts/{id}/members             members grid (server-side paged)
    PATCH/PUT /admin/edgar/cohorts/{id}                ALWAYS 409 — a saved cohort never changes
    POST /admin/edgar/cohorts/{id}/copy                "copy and edit": a NEW cohort
    POST /admin/edgar/cohorts/{id}/runs                run kind 'fetch' (cap optional); 'extract' reserved for B2
    GET  /admin/edgar/inventory/{run_id}               template-study concepts + per-issuer label dictionary

  Bulk download (bulkfetchui) — QUEUED runs, executed by a worker process, never by the API:
    GET  /admin/edgar/bulk/institutions[?fresh=1]      issuer groups, their filer entities, selected counts by year,
                                                       downloaded / stored (fresh=1 bypasses the 60 s cache)
    POST /admin/edgar/bulk/preview                     the dry run: counts per institution/year/entity, ETA, storage
    POST /admin/edgar/bulk/runs                        queue a bulk_fetch run (target laptop | render)
    POST /admin/edgar/bulk/runs/refresh-index          queue a refresh_index run
    GET  /admin/edgar/bulk/runs | /admin/edgar/bulk/runs/{id}   status, progress, ETA, "worker lost"
    POST /admin/edgar/bulk/runs/{id}/stop | /cancel | /requeue
    GET  /admin/edgar/bulk/workers                     last heartbeat per target; Render detection (read-only)
                                                       + laptop_start_command, laptop_instructions (the dialog's steps)

Everything here is GLOBAL public SEC reference data (the manifest, the issuer
table, the pipeline runs) — no org_id is read or accepted anywhere. The gate is
``services.rbac.is_super_admin`` on the caller's principal, the same shape as
routers/pricing_admin.py. Every response publishes the permission envelope;
``editable`` lists are empty arrays whenever the caller cannot write (never
reached today — a non-super-admin gets the 403 first — but the shape is the
contract the client renders from).
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from services import edgar_bulk_queue as bulkq
from services import edgar_cohorts as cohorts
from services import edgar_inventory as inventory
from services import edgar_pipeline, edgar_pipeline_admin as admin
from services.database import get_pool
from services.rbac import is_super_admin, load_principal
from services.users import ensure_user

router = APIRouter(tags=["admin", "edgar"])

PERMISSION = "super_admin"


async def _require_super_admin(request: Request) -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        actor_id = await ensure_user(conn, request)
        principal = await load_principal(conn, actor_id)
    if not is_super_admin(principal):
        raise HTTPException(status_code=403, detail="Super Admin access required")
    return actor_id


def _permissions(can_write: bool) -> dict:
    return {
        "can_read": True,
        "can_write": can_write,
        "is_super_admin": True,
        "read_permission": PERMISSION,
        "write_permission": PERMISSION,
    }


def _bad_input(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


# ── Models (extra='forbid': no org_id, no stray fields) ─────────────────────
class IssuerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_status: str | None = None
    credit_entity: str | None = None
    notes: str | None = None


class IssuerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filer_cik: str
    issuer_group: str
    filer_name: str
    filer_role: str
    credit_entity: str
    include_status: str
    notes: str | None = None


class RunNow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fetch_cap: int = Field(ge=0, le=edgar_pipeline.MAX_FETCH_CAP)


class CohortPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    definition: dict


class CohortCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    purpose: str | None = None
    definition: dict


class TemplateStudy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=200)
    purpose: str | None = None
    seed: int = Field(default=cohorts.DEFAULT_SEED, ge=0, le=2**31 - 1)
    per_stratum: int = Field(default=cohorts.TEMPLATE_STUDY_PER_STRATUM, ge=1, le=100)


class CohortCopy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    purpose: str | None = None
    add: list[str] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)


class CohortRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_kind: Literal["fetch", "extract"] = "fetch"
    fetch_cap: int | None = Field(default=None, ge=1, le=edgar_pipeline.MAX_FETCH_CAP)


# ── Reads ───────────────────────────────────────────────────────────────────
@router.get("/admin/edgar/filings")
async def edgar_filings(
    request: Request,
    issuer_group: str | None = None,
    form_type: str | None = None,
    status: str | None = None,
    document_kind: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    quarter: str | None = None,
    sort: str = "filing_date",
    direction: str = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=admin.MAX_PAGE_SIZE),
):
    await _require_super_admin(request)
    filters = {
        "issuer_group": issuer_group, "form_type": form_type, "status": status,
        "document_kind": document_kind, "date_from": date_from, "date_to": date_to,
        "quarter": quarter,
    }
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            result = await admin.list_filings(
                conn, filters=filters, sort=sort, direction=direction,
                page=page, page_size=page_size,
            )
        except admin.EdgarAdminInputError as exc:
            raise _bad_input(exc) from exc
        groups = await admin.issuer_groups(conn)
        quarters = await admin.quarters(conn)
    return {
        **result,
        "filters": {k: v for k, v in filters.items() if v},
        "permissions": _permissions(can_write=False),
        "vocabularies": {
            "editable": [],
            "inline_editable": [],
            "statuses": admin.STATUS_LABELS,
            "document_kinds": admin.DOCUMENT_KIND_LABELS,
            "form_types": list(admin.FORM_TYPES),
            "issuer_groups": groups,
            "unlisted_group": admin.UNLISTED_GROUP,
            "quarters": quarters,
            "sortable": list(admin.SORT_COLUMNS),
            "max_page_size": admin.MAX_PAGE_SIZE,
        },
    }


@router.get("/admin/edgar/progress")
async def edgar_progress(request: Request):
    await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await admin.progress(conn)
    return {
        **result,
        "permissions": _permissions(can_write=True),
        "vocabularies": {
            "editable": [],
            "inline_editable": [],
            "statuses": admin.STATUS_LABELS,
            "run_now": {
                "label": "Run default policy now (newest first)",
                "description": ("The nightly behaviour: the active selection policy's filings, newest "
                                "first, going backward. To fetch particular filings, build a cohort on "
                                "the Filings tab and run it from the Cohorts tab."),
                "default_fetch_cap": edgar_pipeline.fetch_cap_default(),
                "max_fetch_cap": edgar_pipeline.MAX_FETCH_CAP,
            },
        },
    }


@router.get("/admin/edgar/issuers")
async def edgar_issuers(request: Request):
    await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        issuers = await admin.list_issuers(conn)
        unlisted = await admin.unlisted_filers(conn)
    return {
        "rows": issuers,
        "unlisted_filers": unlisted,
        "permissions": _permissions(can_write=True),
        "vocabularies": {
            "editable": list(admin.ISSUER_EDITABLE),
            "inline_editable": [],
            "include_status": admin.INCLUDE_STATUS_LABELS,
            "filer_roles": admin.FILER_ROLE_LABELS,
        },
    }


# ── Writes ──────────────────────────────────────────────────────────────────
@router.put("/admin/edgar/issuers/{cik}")
async def edgar_update_issuer(cik: str, body: IssuerUpdate, request: Request):
    await _require_super_admin(request)
    changes = body.model_dump(exclude_unset=True)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            result = await admin.update_issuer(conn, cik, changes)
        except admin.EdgarAdminInputError as exc:
            raise _bad_input(exc) from exc
    if not result:
        raise HTTPException(status_code=404, detail=f"issuer {cik} is not in the issuer table")
    return result


@router.post("/admin/edgar/issuers", status_code=201)
async def edgar_add_issuer(body: IssuerCreate, request: Request):
    await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            return await admin.add_issuer(conn, body.model_dump())
        except admin.EdgarAdminInputError as exc:
            raise _bad_input(exc) from exc


@router.post("/admin/edgar/runs", status_code=202)
async def edgar_run_now(body: RunNow, request: Request):
    """Launch the same job the nightly workflow launches. Returns at once; the
    run row (status launched / refused / launch_failed) is the answer."""
    actor_id = await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await edgar_pipeline.launch_pipeline_run(
            conn, trigger_source="manual", requested_by=actor_id, fetch_cap=body.fetch_cap,
        )
    return {"run": row}


# ── Cohorts (edgarcohorts) ──────────────────────────────────────────────────
def _cohort_vocab(can_write: bool) -> dict:
    return {
        "editable": [],                 # a saved cohort is never edited — copy it instead
        "inline_editable": [],
        "sampling_methods": cohorts.SAMPLING_METHODS if can_write else {},
        "stratify_by": cohorts.STRATIFY_BY,
        "eras": [label for label, _a, _b in cohorts.ERAS],
        "max_size": cohorts.MAX_COHORT_SIZE,
        "run_kinds": {"fetch": "Fetch this cohort", "extract": "Extract (available in B2)"},
        "run_kinds_available": ["fetch"],
        "statuses": admin.STATUS_LABELS,
        "document_kinds": admin.DOCUMENT_KIND_LABELS,
        "template_study": {
            "label": "Build template study",
            "per_stratum": cohorts.TEMPLATE_STUDY_PER_STRATUM,
            "description": ("Every issuer with include status 'yes', sampled across the three eras, "
                            f"about {cohorts.TEMPLATE_STUDY_PER_STRATUM} per issuer per era (oversampled: "
                            "document kind is only known after fetch)."),
        },
    }


def _cohort_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/admin/edgar/cohorts")
async def edgar_cohorts(request: Request):
    await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await cohorts.list_cohorts(conn)
    return {"rows": rows, "permissions": _permissions(can_write=True), "vocabularies": _cohort_vocab(True)}


@router.post("/admin/edgar/cohorts/preview")
async def edgar_cohort_preview(body: CohortPreview, request: Request):
    await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            return await cohorts.preview(conn, body.definition)
        except cohorts.CohortInputError as exc:
            raise _cohort_error(exc) from exc


@router.post("/admin/edgar/cohorts", status_code=201)
async def edgar_cohort_create(body: CohortCreate, request: Request):
    actor_id = await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            return {"cohort": await cohorts.create_cohort(
                conn, name=body.name, purpose=body.purpose, definition=body.definition, created_by=actor_id)}
        except cohorts.CohortInputError as exc:
            raise _cohort_error(exc) from exc


@router.post("/admin/edgar/cohorts/presets/template-study/preview")
async def edgar_template_study_preview(body: TemplateStudy, request: Request):
    await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        defn = cohorts.template_study_definition(seed=body.seed, per_stratum=body.per_stratum)
        return await cohorts.preview(conn, defn)


@router.post("/admin/edgar/cohorts/presets/template-study", status_code=201)
async def edgar_template_study_create(body: TemplateStudy, request: Request):
    actor_id = await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        defn = cohorts.template_study_definition(seed=body.seed, per_stratum=body.per_stratum)
        try:
            return {"cohort": await cohorts.create_cohort(
                conn, name=body.name or "Template study", kind="template_study",
                purpose=body.purpose or ("Template study: every 'yes' issuer across 2019-2021, 2022-2023 "
                                         "and 2024-2026, for the inventory pass"),
                definition=defn, created_by=actor_id)}
        except cohorts.CohortInputError as exc:
            raise _cohort_error(exc) from exc


@router.get("/admin/edgar/cohorts/{cohort_id}")
async def edgar_cohort_detail(cohort_id: UUID, request: Request):
    await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        cohort = await cohorts.get_cohort(conn, cohort_id)
        if cohort is None or cohort.get("sealed_at") is None:
            raise HTTPException(status_code=404, detail=f"cohort {cohort_id} not found")
        return {
            "cohort": cohort,
            "status_counts": await cohorts.status_counts(conn, cohort_id),
            "strata": await cohorts.stratum_counts(conn, cohort_id),
            "runs": await cohorts.cohort_runs(conn, cohort_id),
            "inventory_runs": await inventory.inventory_runs(conn, cohort_id),
            "permissions": _permissions(can_write=True),
            "vocabularies": _cohort_vocab(True),
        }


@router.get("/admin/edgar/cohorts/{cohort_id}/members")
async def edgar_cohort_members(cohort_id: UUID, request: Request, status: str | None = None,
                               page: int = Query(1, ge=1),
                               page_size: int = Query(50, ge=1, le=admin.MAX_PAGE_SIZE)):
    await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            result = await cohorts.cohort_members_page(conn, cohort_id, status=status, page=page,
                                                      page_size=page_size)
        except cohorts.CohortInputError as exc:
            raise _cohort_error(exc) from exc
    return {**result, "permissions": _permissions(can_write=False),
            "vocabularies": {"editable": [], "inline_editable": [], "statuses": admin.STATUS_LABELS,
                             "document_kinds": admin.DOCUMENT_KIND_LABELS}}


@router.patch("/admin/edgar/cohorts/{cohort_id}")
@router.put("/admin/edgar/cohorts/{cohort_id}")
async def edgar_cohort_edit(cohort_id: UUID, request: Request):
    """A saved cohort never changes. Refused for everyone — copy it instead."""
    await _require_super_admin(request)
    raise HTTPException(status_code=409, detail=(
        f"cohort {cohort_id} is frozen: a saved cohort never changes. Use "
        f"POST /admin/edgar/cohorts/{cohort_id}/copy to make a new one."))


@router.post("/admin/edgar/cohorts/{cohort_id}/copy", status_code=201)
async def edgar_cohort_copy(cohort_id: UUID, body: CohortCopy, request: Request):
    actor_id = await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            return {"cohort": await cohorts.copy_cohort(
                conn, cohort_id, name=body.name, purpose=body.purpose, add=body.add, remove=body.remove,
                created_by=actor_id)}
        except (cohorts.CohortInputError, LookupError) as exc:
            raise _cohort_error(exc) from exc


@router.post("/admin/edgar/cohorts/{cohort_id}/runs", status_code=202)
async def edgar_cohort_run(cohort_id: UUID, body: CohortRun, request: Request):
    """Launch the same Render job against exactly this cohort's members, in
    cohort order. Returns at once; the run row is the answer."""
    actor_id = await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            row = await cohorts.launch_cohort_run(conn, cohort_id, run_kind=body.run_kind,
                                                  fetch_cap=body.fetch_cap, requested_by=actor_id)
        except edgar_pipeline.RunKindNotAvailable as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except LookupError as exc:
            raise _cohort_error(exc) from exc
    return {"run": row}


@router.get("/admin/edgar/inventory/{run_id}")
async def edgar_inventory_run(run_id: UUID, request: Request):
    await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        runs = None
        from services.database import platform_scope
        async with platform_scope(conn):
            runs = await conn.fetchrow("SELECT * FROM portfolio.edgar_inventory_runs WHERE id = $1", run_id)
            docs = await conn.fetch(
                """SELECT accession_number, issuer_group, document_kind, selection_reason, status, error,
                          terms_tokens_est, product_family, program_supplement, has_payout_table,
                          issue_size, items_accepted, items_rejected, provider_model, cost_usd
                   FROM portfolio.edgar_inventory_documents WHERE run_id = $1
                   ORDER BY issuer_group, accession_number""", run_id)
        if runs is None:
            raise HTTPException(status_code=404, detail=f"inventory run {run_id} not found")
        run = dict(runs)
        run.pop("report", None)
        return {
            "run": run,
            "documents": [dict(d) for d in docs],
            "rows": await inventory.inventory_concepts(conn, run_id),
            "label_dictionary": await inventory.run_label_dictionary(conn, run_id),
            "permissions": _permissions(can_write=False),
            "vocabularies": {"editable": [], "inline_editable": []},
        }


# ── Bulk download (bulkfetchui) ─────────────────────────────────────────────
class BulkParams(BaseModel):
    """The screen's choices. Range checks (8/s ceiling, concurrency, dates
    against the index) live in services.edgar_bulk_queue.validate_params so
    the preview and the queue refuse identically."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    banks: list[str] = Field(default_factory=list)
    date_from: str | None = Field(default=None, alias="from")
    date_to: str | None = Field(default=None, alias="to")
    kinds: list[str] = Field(default_factory=lambda: list(bulkq.DEFAULT_KINDS))
    peek: bool = True
    keep_preliminaries: bool = False
    concurrency: int = bulkq.DEFAULT_CONCURRENCY
    max_rps: int = 8

    def as_params(self) -> dict:
        return {"banks": self.banks, "from": self.date_from, "to": self.date_to, "kinds": self.kinds,
                "peek": self.peek, "keep_preliminaries": self.keep_preliminaries,
                "concurrency": self.concurrency, "max_rps": self.max_rps}


class BulkRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    params: BulkParams
    target: Literal["laptop", "render"] = bulkq.DEFAULT_TARGET


class RefreshIndexCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: str | None = None
    target: Literal["laptop", "render"] = bulkq.DEFAULT_TARGET


def _bulk_error(exc: Exception) -> HTTPException:
    if isinstance(exc, bulkq.BulkQueueError):
        return HTTPException(status_code=exc.status, detail={"message": str(exc), **exc.extra})
    return HTTPException(status_code=422, detail={"message": str(exc)})


_BULK_ERRORS = (bulkq.BulkQueueError, ValueError)


def _bulk_vocab(can_write: bool) -> dict:
    return {
        "editable": [],
        "inline_editable": [],
        "kinds": bulkq.KIND_OPTIONS,
        "default_kinds": bulkq.DEFAULT_KINDS if can_write else [],
        "targets": bulkq.TARGET_LABELS,
        "default_target": bulkq.DEFAULT_TARGET,
        "statuses": bulkq.STATUS_LABELS,
        "run_kinds": bulkq.RUN_KIND_LABELS,
        "trigger_sources": bulkq.TRIGGER_SOURCE_LABELS,
        "institution_columns": bulkq.INSTITUTION_COLUMNS,
        "max_rps": 8,
        "default_concurrency": bulkq.DEFAULT_CONCURRENCY,
        "max_concurrency": bulkq.MAX_CONCURRENCY,
        "render_refusal": bulkq.RENDER_REFUSAL,
    }


@router.get("/admin/edgar/bulk/institutions")
async def edgar_bulk_institutions(request: Request, fresh: bool = Query(False)):
    """``?fresh=1`` (bulkfetchui3, the screen's Refresh) bypasses the 60 s
    cache. Same route, same gate."""
    await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await bulkq.institutions(conn, use_cache=not fresh)
    return {**result, "permissions": _permissions(can_write=True), "vocabularies": _bulk_vocab(True)}


@router.post("/admin/edgar/bulk/preview")
async def edgar_bulk_preview(body: BulkParams, request: Request):
    """The dry run for the screen. Read-only: writes nothing."""
    await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            return await bulkq.preview(conn, body.as_params())
        except _BULK_ERRORS as exc:
            raise _bulk_error(exc) from exc


@router.post("/admin/edgar/bulk/runs", status_code=202)
async def edgar_bulk_queue_run(body: BulkRunCreate, request: Request):
    """QUEUE a bulk download for a worker. Laptop: always queued (with
    worker_online=false and the start command when no worker is heartbeating;
    worker_online=true and worker_host when one is). Every response carries
    laptop_start_command (setting EDGAR_BULK_LAPTOP_WORKER_COMMAND, else the
    working default). Render: 409 and nothing queued unless a Render worker is configured AND
    alive."""
    actor_id = await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            return await bulkq.queue_bulk_run(conn, body.params.as_params(), target=body.target,
                                              requested_by=actor_id)
        except _BULK_ERRORS as exc:
            raise _bulk_error(exc) from exc


@router.post("/admin/edgar/bulk/runs/refresh-index", status_code=202)
async def edgar_bulk_queue_refresh(body: RefreshIndexCreate, request: Request):
    actor_id = await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            return await bulkq.queue_refresh_index(conn, to=body.to, target=body.target, requested_by=actor_id)
        except _BULK_ERRORS as exc:
            raise _bulk_error(exc) from exc


@router.get("/admin/edgar/bulk/runs")
async def edgar_bulk_runs(request: Request, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)):
    await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await bulkq.list_runs(conn, page=page, page_size=page_size)
    return {**result, "permissions": _permissions(can_write=True), "vocabularies": _bulk_vocab(True)}


@router.get("/admin/edgar/bulk/runs/{run_id}")
async def edgar_bulk_run(run_id: UUID, request: Request):
    await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            return {"run": await bulkq.get_run(conn, run_id)}
        except _BULK_ERRORS as exc:
            raise _bulk_error(exc) from exc


_RUN_ACTIONS = {"stop": bulkq.request_stop, "cancel": bulkq.cancel, "requeue": bulkq.requeue}


@router.post("/admin/edgar/bulk/runs/{run_id}/{action}")
async def edgar_bulk_run_action(run_id: UUID, action: str, request: Request):
    await _require_super_admin(request)
    if action not in _RUN_ACTIONS:
        raise HTTPException(status_code=404, detail=f"unknown run action {action!r}; one of {sorted(_RUN_ACTIONS)}")
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            return {"run": await _RUN_ACTIONS[action](conn, run_id)}
        except _BULK_ERRORS as exc:
            raise _bulk_error(exc) from exc


@router.get("/admin/edgar/bulk/workers")
async def edgar_bulk_workers(request: Request):
    await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await bulkq.workers_state(conn)
    return {**result, "permissions": _permissions(can_write=True), "vocabularies": _bulk_vocab(True)}
