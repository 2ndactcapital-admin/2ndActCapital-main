"""Temporary debug endpoints (Sprint 9 hotfix).

GET /debug/user-info verifies the auth->users mapping in production without
requiring the global JWT middleware to have run. REMOVE once the ensure_user
500s are confirmed fixed.
"""

import os

from fastapi import APIRouter, Request

from routers.entities import get_org_id
from services.database import get_pool
from services.permissions import get_user_id

router = APIRouter(tags=["debug"])


@router.get("/debug/user-info")
async def debug_user_info(request: Request):
    # This route is public (see PUBLIC_PATHS), so the auth middleware did not
    # populate request.state.user. Decode the bearer token here if present so
    # the real jwt_sub / user_id are reported.
    claims = getattr(request.state, "user", None)
    if claims is None:
        auth_header = request.headers.get("Authorization", "")
        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() == "bearer" and token:
            try:
                from main import verify_token

                claims = verify_token(token)
                request.state.user = claims
            except Exception as exc:
                return {
                    "jwt_sub": None,
                    "user_id": None,
                    "user_exists_in_db": False,
                    "org_id": None,
                    "token_error": str(exc),
                    "database_url_set": bool(os.environ.get("DATABASE_URL")),
                }
        else:
            claims = {}
            request.state.user = claims

    jwt_sub = claims.get("sub")
    user_id = get_user_id(request)
    org_id = get_org_id(request)

    user_exists = False
    db_error = None
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            found = await conn.fetchval(
                "SELECT 1 FROM users WHERE id = $1", user_id
            )
            user_exists = found is not None
            # Also check by auth0_sub, since the stored id may differ.
            if not user_exists and jwt_sub:
                by_sub = await conn.fetchval(
                    "SELECT 1 FROM users WHERE auth0_sub = $1", jwt_sub
                )
                user_exists = by_sub is not None
    except Exception as exc:
        import traceback

        print(f"ERROR in /debug/user-info db check: {exc}")
        print(traceback.format_exc())
        db_error = str(exc)

    result = {
        "jwt_sub": jwt_sub,
        "user_id": user_id,
        "user_exists_in_db": user_exists,
        "org_id": org_id,
        "database_url_set": bool(os.environ.get("DATABASE_URL")),
    }
    if db_error:
        result["db_error"] = db_error
    return result
