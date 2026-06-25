import { NextResponse } from "next/server";
import { auth0 } from "@/lib/auth0";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// Forward a client-facing Next.js API route to the FastAPI backend, attaching
// the caller's Auth0 access token. Used by routes the browser calls directly
// (notification bell polling, panel actions) — never call FastAPI from the
// client (CLAUDE.md Rule 5).
export async function forwardToApi(
  path,
  { method = "GET", body, searchParams } = {},
) {
  let session;
  try {
    session = await auth0.getSession();
  } catch {
    // ignore
  }
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  let token;
  try {
    const result = await auth0.getAccessToken();
    token = result?.token || result?.accessToken;
  } catch (error) {
    console.error("[apiForward] getAccessToken failed:", error?.message || error);
  }
  if (!token) {
    return NextResponse.json(
      { error: "Not authenticated — please log out and log back in." },
      { status: 401 },
    );
  }

  const url = new URL(API_BASE + path);
  if (searchParams) {
    for (const [key, value] of Object.entries(searchParams)) {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, value);
      }
    }
  }

  try {
    const res = await fetch(url, {
      method,
      headers: {
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        Authorization: `Bearer ${token}`,
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      cache: "no-store",
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      console.error("[apiForward] API error", path, res.status, data);
      return NextResponse.json(
        { error: data.detail || "Request failed" },
        { status: res.status },
      );
    }
    return NextResponse.json(data);
  } catch (error) {
    console.error("[apiForward] fetch threw:", error?.message || error);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
