"""Custody CSV import endpoints — Sprint fee31, Task 3.

Four steps, four endpoints, matching the screen exactly:

    GET  /custody/profiles          which custodians this org can import
    POST /custody/import/inspect    upload → headers + masked sample rows
    POST /custody/import/dry-run    + a column map → the diff, writing NOTHING
    POST /custody/import/commit     the same diff, applied in one transaction
    GET  /custody/batches[/{id}]    what past imports did, incl. exceptions

The upload is re-posted at each step rather than held server-side between them.
A server-side staging area for a file containing full account numbers is a place
those numbers sit at rest outside the protection this sprint was written to
provide, and the alternative costs one more multipart POST.

``org_id`` comes from ``routers.entities.get_org_id`` (JWT claims) on every
route. It is never read from the body and never from the uploaded file.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from routers.entities import get_org_id
from services.custody import (
    ColumnMappingError,
    CustodyError,
    UnknownAdapterError,
    UnknownCustodianError,
    build_adapter,
    load_profiles,
    registered_adapters,
)
from services.custody.importer import (
    READ_PERMISSION,
    WRITE_PERMISSION,
    ImportError_,
    build_plan,
    commit_plan,
    get_batch,
    list_batches,
)
from services.database import get_pool
from services.permissions import get_user_id
from services.rbac import has_permission, require_permission

router = APIRouter(tags=["custody-import"])

#: Refuse a file larger than this before decoding it. The whole file is read
#: into memory to be parsed, so an unbounded upload is an unbounded allocation.
MAX_UPLOAD_BYTES = 32 * 1024 * 1024


def _parse_column_map(raw: str | None) -> dict[str, dict[str, str]] | None:
    """The mapping step posts its choices as a JSON string in a form field.

    Validated into exactly ``{kind: {record_field: source_column}}`` before it
    reaches the adapter. The values become dictionary lookups against the file's
    headers, never SQL identifiers, but a malformed shape here would surface
    deep inside the parser as a confusing per-row failure rather than as "your
    mapping is the wrong shape".
    """
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail=f"column_map is not valid JSON: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=400,
            detail="column_map must be an object keyed by record kind "
                   "('account', 'balance', 'flow')",
        )
    cleaned: dict[str, dict[str, str]] = {}
    for kind, mapping in parsed.items():
        if kind not in ("account", "balance", "flow"):
            raise HTTPException(
                status_code=400,
                detail=f"unknown record kind {kind!r} in column_map; expected "
                       f"'account', 'balance' or 'flow'",
            )
        if mapping is None:
            continue
        if not isinstance(mapping, dict):
            raise HTTPException(
                status_code=400,
                detail=f"column_map[{kind!r}] must be an object mapping record "
                       f"fields to source column names",
            )
        cleaned[kind] = {
            str(k): str(v) for k, v in mapping.items() if v not in (None, "")
        }
    return cleaned


async def _read_upload(file: UploadFile) -> bytes:
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="the uploaded file is empty")
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"file is larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)}MB "
                   f"import limit",
        )
    return contents


def _map_custody_error(exc: CustodyError) -> HTTPException:
    """Typed custody errors → the status that tells the caller who can fix it.

    UnknownCustodianError is a 404 on a *configuration* the org can add itself;
    UnknownAdapterError is a 500 because the settings are fine and the code is
    not. Flattening both to 400 would send an operator to fix a profile that is
    already correct.
    """
    if isinstance(exc, UnknownCustodianError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, UnknownAdapterError):
        return HTTPException(status_code=500, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/custody/profiles")
async def custody_profiles(request: Request):
    """Custodian profiles available to this org, plus the caller's write flag.

    Returns the ENVELOPE ``{profiles, adapters, permissions}`` so the screen
    renders its commit button from ``permissions.can_write`` alone rather than
    inferring permission from a 403 it has not made yet.
    """
    org_id = get_org_id(request)
    pool = await get_pool()
    user_id = get_user_id(request)
    await require_permission(pool, user_id, org_id, READ_PERMISSION)
    can_write = await has_permission(pool, user_id, org_id, WRITE_PERMISSION)

    async with pool.acquire() as conn:
        profiles, org_codes = await load_profiles(conn, org_id)

    return {
        "profiles": [
            {
                "custodian_code": code,
                "label": profile.get("label") or code,
                "adapter": profile.get("adapter"),
                "source_system": profile.get("source_system") or code,
                "column_map": profile.get("column_map") or {},
                "is_default": code not in org_codes,
            }
            for code, profile in sorted(profiles.items())
        ],
        "adapters": registered_adapters(),
        "permissions": {"can_write": can_write},
    }


@router.post("/custody/import/inspect")
async def inspect_upload(
    request: Request,
    custodian_code: str = Form(...),
    file: UploadFile = File(...),
):
    """Step 1→2: what columns does this file have, and what does it look like?

    The sample rows come back with anything account-number-shaped MASKED. The
    operator maps by column name and by the shape of neighbouring values; seeing
    real account numbers is not needed for that, and this response would
    otherwise echo the raw file into a browser and a server access log.
    """
    org_id = get_org_id(request)
    pool = await get_pool()
    user_id = get_user_id(request)
    await require_permission(pool, user_id, org_id, WRITE_PERMISSION)

    contents = await _read_upload(file)
    async with pool.acquire() as conn:
        try:
            adapter, profile = await build_adapter(
                conn, org_id, custodian_code,
                file_bytes=contents, filename=file.filename,
            )
        except CustodyError as exc:
            raise _map_custody_error(exc) from exc

    return {
        "custodian_code": custodian_code,
        "label": profile.label,
        "adapter": profile.adapter_key,
        "filename": file.filename,
        "headers": adapter.headers,
        "row_count": adapter.row_count,
        "sample_rows": adapter.sample_rows(),
        "suggested_column_map": profile.column_map,
    }


@router.post("/custody/import/dry-run")
async def dry_run(
    request: Request,
    custodian_code: str = Form(...),
    column_map: str | None = Form(default=None),
    file: UploadFile = File(...),
):
    """Step 3: the full diff. Writes nothing to the account tables.

    New accounts, changed balances and unmatched rows are three separate lists
    in the response, not one merged list with a status column — the sprint asks
    for them shown separately because they are three different decisions.
    """
    org_id = get_org_id(request)
    pool = await get_pool()
    user_id = get_user_id(request)
    await require_permission(pool, user_id, org_id, WRITE_PERMISSION)

    contents = await _read_upload(file)
    mapping = _parse_column_map(column_map)

    async with pool.acquire() as conn:
        try:
            plan = await build_plan(
                conn, org_id=org_id, custodian_code=custodian_code,
                file_bytes=contents, filename=file.filename,
                column_map_override=mapping,
            )
        except (ColumnMappingError, ImportError_) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except CustodyError as exc:
            raise _map_custody_error(exc) from exc

    return {"committed": False, **plan.to_json()}


@router.post("/custody/import/commit", status_code=201)
async def commit(
    request: Request,
    custodian_code: str = Form(...),
    column_map: str | None = Form(default=None),
    file: UploadFile = File(...),
):
    """Step 4: apply the same plan the dry-run showed, in one transaction.

    The plan is rebuilt here rather than trusted from the client. A plan posted
    back by the browser would be a set of database writes chosen by the caller —
    including which entity each account attaches to — and re-deriving it costs
    one parse.
    """
    org_id = get_org_id(request)
    pool = await get_pool()
    user_id = get_user_id(request)
    await require_permission(pool, user_id, org_id, WRITE_PERMISSION)

    contents = await _read_upload(file)
    mapping = _parse_column_map(column_map)

    async with pool.acquire() as conn:
        try:
            plan = await build_plan(
                conn, org_id=org_id, custodian_code=custodian_code,
                file_bytes=contents, filename=file.filename,
                column_map_override=mapping,
            )
            result = await commit_plan(
                conn, org_id=org_id, plan=plan, imported_by=user_id
            )
        except (ColumnMappingError, ImportError_) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except CustodyError as exc:
            raise _map_custody_error(exc) from exc

    return {"committed": True, **result, "plan": plan.to_json()}


@router.get("/custody/batches")
async def batches(request: Request, limit: int = 50):
    org_id = get_org_id(request)
    pool = await get_pool()
    user_id = get_user_id(request)
    await require_permission(pool, user_id, org_id, READ_PERMISSION)
    can_write = await has_permission(pool, user_id, org_id, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        rows = await list_batches(conn, org_id, min(max(limit, 1), 200))
    return {"rows": rows, "permissions": {"can_write": can_write}}


@router.get("/custody/batches/{batch_id}")
async def batch_detail(request: Request, batch_id: str):
    """One batch and its exception list — the "visible exception list" surface."""
    org_id = get_org_id(request)
    pool = await get_pool()
    user_id = get_user_id(request)
    await require_permission(pool, user_id, org_id, READ_PERMISSION)
    async with pool.acquire() as conn:
        batch = await get_batch(conn, org_id, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="batch not found")
    return batch
