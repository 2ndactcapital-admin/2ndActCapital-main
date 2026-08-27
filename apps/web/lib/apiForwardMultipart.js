import { NextResponse } from "next/server";
import { getRequestAuthClient } from "@/lib/authServer";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// Forward a multipart/form-data POST to FastAPI with the caller's access token.
//
// apiForward (the JSON one) cannot be reused here: it JSON.stringifies the body
// and sets Content-Type, both of which destroy a multipart payload — the
// boundary token has to survive intact or the backend sees one blob instead of
// a file plus fields.
//
// The FormData is re-read from the incoming request and re-posted rather than
// streamed through, so this route never persists the upload anywhere. That is
// deliberate: a custodial export contains full account numbers, and a temp file
// on the web tier would be exactly the at-rest copy the import pipeline was
// written to avoid creating.
export async function forwardMultipartToApi(path, request) {
  const authClient = await getRequestAuthClient();

  let session;
  try {
    session = await authClient.getSession();
  } catch {
    // ignore — treated as unauthenticated below
  }
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  let token;
  try {
    const result = await authClient.getAccessToken();
    token = result?.token || result?.accessToken;
  } catch (error) {
    console.error(
      "[apiForwardMultipart] getAccessToken failed:",
      error?.message || error,
    );
  }
  if (!token) {
    return NextResponse.json(
      { error: "Not authenticated — please log out and log back in." },
      { status: 401 },
    );
  }

  let formData;
  try {
    formData = await request.formData();
  } catch (error) {
    return NextResponse.json(
      { error: `Could not read the upload: ${error.message}` },
      { status: 400 },
    );
  }

  try {
    const res = await fetch(new URL(API_BASE + path), {
      method: "POST",
      // No Content-Type: fetch derives it, with the boundary, from the FormData.
      headers: { Authorization: `Bearer ${token}` },
      body: formData,
      cache: "no-store",
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      // Status and path only. The request body is a custodial export; logging
      // it here would put full account numbers in the Vercel function logs.
      console.error("[apiForwardMultipart] API error", path, res.status);
      return NextResponse.json(
        { error: data.detail || "Request failed" },
        { status: res.status },
      );
    }
    return NextResponse.json(data);
  } catch (error) {
    console.error("[apiForwardMultipart] fetch threw:", error?.message || error);
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
