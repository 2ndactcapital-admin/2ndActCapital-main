import { NextResponse } from "next/server";
import { auth0 } from "@/lib/auth0";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function POST(request) {
  let session;
  try {
    session = await auth0.getSession();
  } catch {
    // ignore
  }
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  let dealId, vote;
  try {
    ({ dealId, vote } = await request.json());
  } catch {
    return NextResponse.json({ error: "Invalid request body" }, { status: 400 });
  }

  if (!dealId || (vote !== 1 && vote !== -1)) {
    console.error("[vote] bad request", { dealId, vote });
    return NextResponse.json(
      { error: "dealId and vote (1 or -1) are required" },
      { status: 400 }
    );
  }

  let token;
  try {
    const result = await auth0.getAccessToken();
    token = result?.token || result?.accessToken;
  } catch (error) {
    console.error("[vote] getAccessToken failed:", error?.message || error);
  }
  if (!token) {
    console.error("[vote] no access token — user must re-authenticate");
    return NextResponse.json(
      { error: "Not authenticated — please log out and log back in." },
      { status: 401 }
    );
  }

  try {
    const res = await fetch(`${API_BASE}/api/v1/deals/${dealId}/vote`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ vote }),
      cache: "no-store",
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      console.error("[vote] API error", res.status, body);
      return NextResponse.json(
        { error: body.detail || "Vote failed" },
        { status: res.status }
      );
    }
    const data = await res.json();
    return NextResponse.json({ ok: true, summary: data });
  } catch (error) {
    console.error("[vote] fetch threw:", error?.message || error);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
