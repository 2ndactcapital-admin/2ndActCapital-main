import { NextResponse } from "next/server";
import { forwardToApi } from "@/lib/apiForward";
import { isEntityId, reduceLookup, NOT_AVAILABLE } from "@/lib/entityContext.mjs";

export const dynamic = "force-dynamic";

// NAV2 — the entity chip's name lookup. Forwards to the EXISTING GET /api/v1/entities/{id} (gate unchanged:
// require_entity_access — staff any entity in the org, a member only their own family, 404 otherwise) and returns
// ONLY { id, display_name, entity_type }. Every non-success — missing, forbidden (404), malformed (422), anything
// else — collapses to ONE answer, 404 "Not available", so the chip can never tell missing from forbidden.
export async function GET(request, { params }) {
  const { id } = await params;
  const notAvailable = () => NextResponse.json({ error: NOT_AVAILABLE }, { status: 404 });
  if (!isEntityId(id)) return notAvailable();
  const res = await forwardToApi(`/api/v1/entities/${encodeURIComponent(id)}`);
  if (res.status === 401) return res;
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  const entity = reduceLookup(res.ok, data);
  if (!entity || entity.id !== id) return notAvailable();
  return NextResponse.json(entity, { status: 200 });
}
