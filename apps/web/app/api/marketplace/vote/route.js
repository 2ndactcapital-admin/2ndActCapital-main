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

  let token;
  try {
    const result = await auth0.getAccessToken();
    token = result?.token || result?.accessToken;
  } catch {
    // proceed without token — API will reject with 401
  }

  try {
    const res = await fetch(`${API_BASE}/api/v1/deals/${dealId}/vote`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ vote }),
      cache: "no-store",
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      return NextResponse.json(
        { error: body.detail || "Vote failed" },
        { status: res.status }
      );
    }
    const data = await res.json();
    return NextResponse.json({ ok: true, summary: data });
  } catch (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
