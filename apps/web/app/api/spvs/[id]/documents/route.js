import { NextResponse } from "next/server";
import { getRequestAuthClient } from "@/lib/authServer";
import { forwardToApi } from "@/lib/apiForward";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function GET(request, { params }) {
  const { id } = await params;
  return forwardToApi(`/api/v1/spvs/${id}/documents`);
}

export async function POST(request, { params }) {
  // Host-aware Auth0 client: admin.hollisworks.com resolves to the Hollisworks
  // tenant, every other host to the existing 2nd Act client, unchanged.
  const authClient = await getRequestAuthClient();
  const { id } = await params;
  let token;
  try {
    const session = await authClient.getSession();
    if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    const result = await authClient.getAccessToken();
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
