import { NextResponse } from "next/server";
import { getRequestAuthClient } from "@/lib/authServer";

// REP5 — the browser's way to an account's model routes (Rule 5). Forwards
// ONLY /api/accounts/<id>/model… and /api/accounts/<id>/proposals to
// /api/v1/accounts/… with the caller's own token (host-aware Auth0 client);
// every other path is a 404 here. No org_id is ever added on this side.
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
  if (segments.length < 2 || !["model", "proposals"].includes(segments[1])) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }
  const url = new URL(`${API_BASE}/api/v1/accounts/${segments.join("/")}`);
  new URL(request.url).searchParams.forEach((v, k) => url.searchParams.set(k, v));

  const headers = { Authorization: `Bearer ${token}` };
  let body;
  if (!["GET", "HEAD"].includes(request.method)) {
    body = await request.text();
    if (body) headers["Content-Type"] = "application/json";
  }
  const res = await fetch(url, { method: request.method, headers, body: body || undefined, cache: "no-store" });
  const type = res.headers.get("content-type") || "";
  if (res.ok && type.startsWith("application/pdf")) {
    return new NextResponse(await res.arrayBuffer(), {
      status: 200,
      headers: {
        "Content-Type": "application/pdf",
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
