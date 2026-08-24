import { NextResponse } from "next/server";
import { getRequestAuthClient } from "@/lib/authServer";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// Chancery intake proxy (Phase 1 + Phase 10). Forwards a multipart upload — one
// or more files, plus the optional Phase-10 `is_vdr` flag — to FastAPI
// POST /api/v1/documents, attaching the caller's Auth0 token. Rule 5: the
// browser never calls FastAPI directly. forwardToApi is JSON-only, so this route
// streams the FormData through itself (no Content-Type header — fetch sets the
// multipart boundary).
export async function POST(request) {
  // Host-aware Auth0 client: admin.hollisworks.com resolves to the Hollisworks
  // tenant, every other host to the existing 2nd Act client, unchanged.
  const authClient = await getRequestAuthClient();
  let session;
  try {
    session = await authClient.getSession();
  } catch {
    // ignore
  }
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  let token;
  try {
    const result = await authClient.getAccessToken();
    token = result?.token || result?.accessToken;
  } catch (error) {
    console.error("[api/documents] getAccessToken failed:", error?.message || error);
  }
  if (!token) {
    return NextResponse.json(
      { error: "Not authenticated — please log out and log back in." },
      { status: 401 },
    );
  }

  let form;
  try {
    form = await request.formData();
  } catch {
    return NextResponse.json({ error: "Expected a multipart upload." }, { status: 400 });
  }

  try {
    const res = await fetch(new URL(API_BASE + "/api/v1/documents"), {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: form,
      cache: "no-store",
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      console.error("[api/documents] API error", res.status, data);
      return NextResponse.json(
        { error: data.detail || "Upload failed" },
        { status: res.status },
      );
    }
    return NextResponse.json(data);
  } catch (error) {
    console.error("[api/documents] fetch threw:", error?.message || error);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
