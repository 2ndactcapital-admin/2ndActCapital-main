import { NextResponse } from "next/server";
import { getRequestAuthClient } from "@/lib/authServer";

// CPSIM01 — Admin → Counterparties (Super Admin). Forwards to /api/v1/admin/counterparties/<path> with the caller's own
// token (host-aware Auth0 client, Rule 5). No org_id is ever added on this side.
// CPSIM02 — a book upload is multipart (forwarded byte for byte with its own Content-Type), and a template / export is
// a file (CSV or JSON with a Content-Disposition), passed through as-is; everything else stays JSON.
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
  const url = new URL(`${API_BASE}/api/v1/admin/counterparties${segments.length ? "/" + segments.join("/") : ""}`);
  new URL(request.url).searchParams.forEach((v, k) => url.searchParams.set(k, v));

  const headers = { Authorization: `Bearer ${token}` };
  let body;
  if (!["GET", "HEAD"].includes(request.method)) {
    const type = request.headers.get("content-type") || "";
    if (type.startsWith("multipart/form-data")) {
      body = await request.arrayBuffer();
      headers["Content-Type"] = type;
    } else {
      body = await request.text();
      if (body) headers["Content-Type"] = "application/json";
    }
  }
  const res = await fetch(url, { method: request.method, headers, body: body || undefined, cache: "no-store" });
  const disposition = res.headers.get("content-disposition");
  if (res.ok && disposition) {
    return new NextResponse(await res.arrayBuffer(), {
      status: res.status,
      headers: { "Content-Type": res.headers.get("content-type") || "application/octet-stream",
        "Content-Disposition": disposition },
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
export const PUT = forward;
export const POST = forward;
