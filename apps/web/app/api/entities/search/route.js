import { NextResponse } from "next/server";
import { auth0 } from "@/lib/auth0";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// Forward the raw query string to preserve repeated entity_type params.
// forwardToApi uses searchParams.set() which collapses duplicate keys.
export async function GET(request) {
  let session;
  try {
    session = await auth0.getSession();
  } catch {}
  if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  let token;
  try {
    const result = await auth0.getAccessToken();
    token = result?.token || result?.accessToken;
  } catch {}
  if (!token) return NextResponse.json({ error: "Not authenticated" }, { status: 401 });

  const rawSearch = new URL(request.url).search;
  try {
    const res = await fetch(`${API_BASE}/api/v1/entities/search${rawSearch}`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: err.message }, { status: 500 });
  }
}
