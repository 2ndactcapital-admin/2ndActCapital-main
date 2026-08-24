import { NextResponse } from "next/server";
import { getRequestAuthClient } from "@/lib/authServer";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// Sprint 31 — proxy for the SSVI surface calibration. Rule 5: the browser never
// calls FastAPI directly.
//
// This does NOT use `forwardToApi`. That helper collapses every error body to
// `{ error: detail }`, which would throw away the machine-readable `status`
// ("insufficient_data", "quality_gate_failed", …) the results page needs to
// render a first-class failure state. Here the backend's JSON body is passed
// through verbatim, status code included.
//
// Backend budget is 90s (it returns a typed 504 past that). Allow more here so
// the platform does not cut the connection first and turn a typed timeout into
// an opaque one.
export const maxDuration = 120;
export const dynamic = "force-dynamic";

export async function POST(request) {
  // Host-aware Auth0 client: admin.hollisworks.com resolves to the Hollisworks
  // tenant, every other host to the existing 2nd Act client, unchanged.
  const authClient = await getRequestAuthClient();
  let session;
  try {
    session = await authClient.getSession();
  } catch {
    // treated as unauthenticated below
  }
  if (!session) {
    return NextResponse.json(
      { status: "unauthorized", detail: "Not signed in." },
      { status: 401 },
    );
  }

  let token;
  try {
    const result = await authClient.getAccessToken();
    token = result?.token || result?.accessToken;
  } catch (error) {
    console.error("[surface] getAccessToken failed:", error?.message || error);
  }
  if (!token) {
    return NextResponse.json(
      {
        status: "unauthorized",
        detail: "Not authenticated — please log out and log back in.",
      },
      { status: 401 },
    );
  }

  let body;
  try {
    body = await request.json();
  } catch {
    body = {};
  }

  // Only the ticker is forwarded. org_id is resolved server-side by FastAPI
  // from the session and must never be accepted from the browser.
  const payload = { ticker: body?.ticker };

  try {
    const res = await fetch(`${API_BASE}/api/v1/admin/pricing/surface`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(payload),
      cache: "no-store",
    });

    const data = await res.json().catch(() => null);
    if (data === null) {
      return NextResponse.json(
        {
          status: "unexpected_error",
          detail: `API returned a non-JSON response (${res.status}).`,
        },
        { status: res.status === 200 ? 502 : res.status },
      );
    }
    if (!res.ok) {
      console.error("[surface] API error", res.status, data);
      // FastAPI's own 401/403 come back as {detail: "..."} with no `status`.
      if (!data.status) data.status = res.status === 403 ? "forbidden" : "error";
    }
    return NextResponse.json(data, { status: res.status });
  } catch (error) {
    console.error("[surface] fetch threw:", error?.message || error);
    return NextResponse.json(
      { status: "unexpected_error", detail: error?.message || "Request failed." },
      { status: 502 },
    );
  }
}
