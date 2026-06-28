import { NextResponse } from "next/server";
import { auth0 } from "@/lib/auth0";
import { forwardToApi } from "@/lib/apiForward";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function GET(request, { params }) {
  const { id } = await params;
  return forwardToApi(`/api/v1/spvs/${id}/documents`);
}

export async function POST(request, { params }) {
  const { id } = await params;
  let token;
  try {
    const session = await auth0.getSession();
    if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    const result = await auth0.getAccessToken();
    token = result?.token || result?.accessToken;
  } catch {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  if (!token) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const formData = await request.formData();
  const res = await fetch(`${API_BASE}/api/v1/spvs/${id}/documents`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: formData,
    cache: "no-store",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return NextResponse.json({ error: data.detail || "Upload failed" }, { status: res.status });
  return NextResponse.json(data);
}
