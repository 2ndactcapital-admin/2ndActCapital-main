import { NextResponse } from "next/server";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// Public (pre-tenant) forwarder for the Hollisworks marketing contact form.
// A prospect is not authenticated, so — unlike lib/apiForward — this attaches
// no bearer token. It forwards to the public FastAPI endpoint, which stores the
// lead under the pre-auth RLS carve-out. Client components still never call
// FastAPI directly (CLAUDE.md Rule 5); they call this route.
export async function POST(request) {
  let body;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid request body" }, { status: 400 });
  }

  try {
    const res = await fetch(`${API_BASE}/api/v1/marketing/contact`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      return NextResponse.json(
        { error: data.detail || "Request failed" },
        { status: res.status },
      );
    }
    return NextResponse.json(data);
  } catch (error) {
    return NextResponse.json({ error: error.message }, { status: 502 });
  }
}
