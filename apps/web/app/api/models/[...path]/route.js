import { NextResponse } from "next/server";
import { getRequestAuthClient } from "@/lib/authServer";

// REP3 — the browser's only way to the models API (Rule 5: client components
// never call FastAPI). Forwards /api/models/<path> to /api/v1/models/<path>
// with the caller's own token, read with the host-aware Auth0 client. JSON and
// multipart bodies are passed through unchanged (an upload is multipart);
// file responses (the template, the upload-log CSV) are streamed back with the
// server's content type and disposition. No org_id is ever added on this side.
// [FIND] a deployment's own request-body limit (Vercel: 4.5 MB) sits below the
// API's 5 MB upload cap; a file between the two is refused here, before the API.
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
  const url = new URL(`${API_BASE}/api/v1/models/${segments.join("/")}`);
  new URL(request.url).searchParams.forEach((v, k) => url.searchParams.set(k, v));

  const headers = { Authorization: `Bearer ${token}` };
  let body;
  if (!["GET", "HEAD"].includes(request.method)) {
    const ct = request.headers.get("content-type") || "";
    if (ct.startsWith("multipart/form-data")) {
      body = await request.arrayBuffer();
      headers["Content-Type"] = ct;
    } else {
      body = await request.text();
      if (body) headers["Content-Type"] = "application/json";
    }
  }
  const res = await fetch(url, { method: request.method, headers, body: body || undefined, cache: "no-store" });
  const rct = res.headers.get("content-type") || "";
  if (res.ok && !rct.includes("application/json")) {
    const out = new NextResponse(await res.arrayBuffer(), { status: res.status });
    out.headers.set("Content-Type", rct || "application/octet-stream");
    const cd = res.headers.get("content-disposition");
    if (cd) out.headers.set("Content-Disposition", cd);
    return out;
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
export const PUT = forward;
export const PATCH = forward;
