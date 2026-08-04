import { NextResponse } from "next/server";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// Public (pre-tenant) forwarder for the shared Login/Enroll firm-search
// interstitial. Forwards the typed name + intent to the public FastAPI matcher
// and relays its {status, redirect_url, org_name, message} verdict. No bearer
// token — the prospect is unauthenticated.
export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const q = searchParams.get("q") || "";
  const intent = searchParams.get("intent") || "login";

  try {
    const url = new URL(`${API_BASE}/api/v1/marketing/firm-search`);
    url.searchParams.set("q", q);
    url.searchParams.set("intent", intent);
    const res = await fetch(url, { cache: "no-store" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      return NextResponse.json(
        { status: "none", message: data.detail || "Request failed" },
        { status: res.status },
      );
    }
    return NextResponse.json(data);
  } catch (error) {
    return NextResponse.json(
      { status: "none", message: error.message },
      { status: 502 },
    );
  }
}
