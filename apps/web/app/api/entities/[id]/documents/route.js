import { NextResponse } from "next/server";
import { getRequestAuthClient } from "@/lib/authServer";
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
  // Host-aware Auth0 client: admin.hollisworks.com resolves to the Hollisworks
  // tenant, every other host to the existing 2nd Act client, unchanged.
  const authClient = await getRequestAuthClient();
  const { id } = await params;

  let token;
  try {
    const result = await authClient.getAccessToken();
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
