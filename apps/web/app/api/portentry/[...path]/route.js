import { NextResponse } from "next/server";
import { getRequestAuthClient } from "@/lib/authServer";
import { portentryAllowed } from "@/lib/portentry.mjs";

// PORTENTRY1 — the browser's only way to the PORTENTRY1 API (Rule 5: client components never call FastAPI).
// /api/portentry/<path> → /api/v1/<path>, ONLY for the allow-listed paths (lib/portentry.mjs) — never an open
// proxy. The caller's own token (host-aware Auth0 client); a CSV upload is forwarded as raw multipart bytes. No
// org_id is ever added on this side.
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function forward(request, { params }) {
  const { path } = await params;
  const rel = (path || []).map((p) => encodeURIComponent(p)).join("/");
  if (!portentryAllowed(request.method, rel)) return NextResponse.json({ error: "Not found" }, { status: 404 });
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
  const url = new URL(`${API_BASE}/api/v1/${rel}`);
  new URL(request.url).searchParams.forEach((v, k) => url.searchParams.set(k, v));
  const headers = { Authorization: `Bearer ${token}` };
  let body;
  if (!["GET", "HEAD"].includes(request.method)) {
    const type = request.headers.get("content-type") || "";
    if (type.startsWith("multipart/form-data")) {
      body = Buffer.from(await request.arrayBuffer());
      headers["Content-Type"] = type;
    } else {
      body = await request.text();
      if (body) headers["Content-Type"] = "application/json";
    }
  }
  const res = await fetch(url, { method: request.method, headers, body: body || undefined, cache: "no-store" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data?.detail;
    return NextResponse.json({ error: typeof detail === "string" ? detail : "Request failed", detail },
      { status: res.status });
  }
  return NextResponse.json(data, { status: res.status });
}

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const PATCH = forward; // BUGFIX4 — the private-investment edit
