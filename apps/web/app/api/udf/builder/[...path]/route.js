import { NextResponse } from "next/server";
import { getRequestAuthClient } from "@/lib/authServer";
import { isForwardable, stripOrg } from "@/lib/udfBuilder.mjs";

// UDFUI1 — the custom-fields builder's only way to the UDF API (Rule 5: client components never call FastAPI).
// Forwards /api/udf/builder/<path> to /api/v1/udf/<path> with the caller's own token (host-aware Auth0 client), ONLY
// for a (method, path) listed in lib/udfBuilder.mjs UDF_ROUTES — anything else is a 404 here. JSON bodies are
// forwarded with any org_id removed (org scope is the session's). Every gate is the FastAPI route's own; a refusal
// comes back with the engine's own reason (`error`) and its structured `detail` (e.g. a delete's references).
// The records export (text/csv) passes through as bytes.
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function forward(request, { params }) {
  const { path } = await params;
  const segs = (path || []).map(String);
  if (!isForwardable(request.method, segs)) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }
  const authClient = await getRequestAuthClient();
  let token;
  try {
    const session = await authClient.getSession();
    if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    const result = await authClient.getAccessToken();
    token = result?.token || result?.accessToken;
  } catch {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  if (!token) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const url = new URL(`${API_BASE}/api/v1/udf/${segs.map((p) => encodeURIComponent(p)).join("/")}`);
  new URL(request.url).searchParams.forEach((v, k) => {
    if (k !== "org_id") url.searchParams.set(k, v);
  });

  const headers = { Authorization: `Bearer ${token}` };
  let body;
  if (!["GET", "HEAD", "DELETE"].includes(request.method)) {
    const raw = await request.json().catch(() => undefined);
    if (raw !== undefined) {
      body = JSON.stringify(stripOrg(raw));
      headers["Content-Type"] = "application/json";
    }
  }
  let res;
  try {
    res = await fetch(url, { method: request.method, headers, body, cache: "no-store" });
  } catch (error) {
    return NextResponse.json({ error: error?.message || "API unreachable" }, { status: 502 });
  }
  const type = res.headers.get("content-type") || "";
  if (res.ok && type.startsWith("text/csv")) {
    return new NextResponse(await res.arrayBuffer(), {
      status: 200,
      headers: {
        "Content-Type": type,
        "Content-Disposition": res.headers.get("content-disposition") || "attachment",
        "Cache-Control": "no-store",
      },
    });
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data?.detail;
    const error = typeof detail === "string" ? detail : detail?.message || "Request failed";
    return NextResponse.json({ error, detail }, { status: res.status });
  }
  return NextResponse.json(data, { status: res.status });
}

export const GET = forward;
export const POST = forward;
export const PATCH = forward;
export const PUT = forward;
export const DELETE = forward;
