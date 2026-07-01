import { NextResponse } from "next/server";
import { auth0 } from "@/lib/auth0";
import { forwardToApi } from "@/lib/apiForward";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function GET(request, { params }) {
  const { id } = await params;
  const { searchParams } = new URL(request.url);
  const sp = {};
  for (const [k, v] of searchParams.entries()) sp[k] = v;
  return forwardToApi(`/api/v1/entities/${id}/documents`, { searchParams: sp });
}

export async function POST(request, { params }) {
  const { id } = await params;

  let token;
  try {
    const result = await auth0.getAccessToken();
    token = result?.token || result?.accessToken;
  } catch {}
  if (!token) return NextResponse.json({ error: "Not authenticated" }, { status: 401 });

  try {
    const formData = await request.formData();
    const res = await fetch(`${API_BASE}/api/v1/entities/${id}/documents`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: formData,
      cache: "no-store",
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: err.message }, { status: 500 });
  }
}
