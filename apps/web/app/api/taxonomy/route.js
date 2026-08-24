import { getRequestAuthClient } from "@/lib/authServer";
import { NextResponse } from "next/server";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function GET() {
  // Host-aware Auth0 client: admin.hollisworks.com resolves to the Hollisworks
  // tenant, every other host to the existing 2nd Act client, unchanged.
  const authClient = await getRequestAuthClient();
  try {
    const result = await authClient.getAccessToken();
    const token = result?.token || result?.accessToken;
    if (!token) {
      return NextResponse.json({ super_classes: [] });
    }
    const res = await fetch(`${API_BASE}/api/v1/taxonomy`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) {
      return NextResponse.json({ super_classes: [] });
    }
    const data = await res.json();
    return NextResponse.json(data, {
      headers: { "Cache-Control": "private, max-age=3600" },
    });
  } catch {
    return NextResponse.json({ super_classes: [] });
  }
}
