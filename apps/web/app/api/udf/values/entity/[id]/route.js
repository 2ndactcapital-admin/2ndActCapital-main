import { forwardToApi } from "@/lib/apiForward";

// NAV3 — an entity's custom-field values: read (hidden fields already dropped by the server) and write (the EXISTING
// PUT /udf/values/entity/{id}: manage_portfolio + the field's resolved access must be 'edit', else 403). The body is
// forwarded as { definition_id, value } only — never an org_id.
export async function GET(request, { params }) {
  const { id } = await params;
  return forwardToApi(`/api/v1/udf/values/entity/${encodeURIComponent(id)}`);
}

export async function PUT(request, { params }) {
  const { id } = await params;
  const raw = await request.json().catch(() => ({}));
  const body = { definition_id: raw?.definition_id, value: raw?.value };
  return forwardToApi(`/api/v1/udf/values/entity/${encodeURIComponent(id)}`, { method: "PUT", body });
}
