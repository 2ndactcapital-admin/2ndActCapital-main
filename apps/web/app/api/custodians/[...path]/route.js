import { NextResponse } from "next/server";
import { getRequestAuthClient } from "@/lib/authServer";

// REP2 — the browser's only way to the custodian capabilities API (Rule 5: client
// components never call FastAPI). Forwards /api/custodians/<path> to
// /api/v1/custodians/<path> with the caller's own token, read with the
// host-aware Auth0 client. JSON only; no org_id is ever added on this side.
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function forward(request, { params }) {
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

  const { path } = await params;
  const segments = (path || []).map((p) => encodeURIComponent(p));
  const url = new URL(`${API_BASE}/api/v1/custodians/${segments.join("/")}`);
  new URL(request.url).searchParams.forEach((v, k) => url.searchParams.set(k, v));

  const headers = { Authorization: `Bearer ${token}` };
  let body;
  if (!["GET", "HEAD"].includes(request.method)) {
    body = await request.text();
    if (body) headers["Content-Type"] = "application/json";
  }
  const res = await fetch(url, { method: request.method, headers, body: body || undefined, cache: "no-store" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data?.detail;
    const error = typeof detail === "string" ? detail : detail?.message || "Request failed";
    return NextResponse.json({ error, detail }, { status: res.status });
  }
  return NextResponse.json(data, { status: res.status });
}

export const GET = forward;
export const PUT = forward;
