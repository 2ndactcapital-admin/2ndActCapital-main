import { forwardToApi } from "@/lib/apiForward";

// The PLATFORM security master — a deliberately separate path from
// /api/portfolio/securities, one layer above the same split in FastAPI.
//
// GET is readable by anyone holding view_portfolio: these rows have no org_id
// and belong to no tenant, and the deployed RLS on them is SELECT USING (true).
//
// POST is Super Admin only, enforced SERVER-SIDE in FastAPI
// (routers/portfolio_securities._require_super_admin_actor → 403). This file
// forwards it and gates nothing — a check here would be a check the browser can
// skip by calling the route directly, which is exactly the reassurance that
// makes a real gate feel optional.
const ALLOWED = [
  "search",
  "security_type",
  "price_coverage",
  "include_merged",
  "limit",
  "offset",
];

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const params = {};
  for (const key of ALLOWED) {
    const value = searchParams.get(key);
    if (value !== null && value !== "") params[key] = value;
  }
  return forwardToApi("/api/v1/portfolio/global-securities", {
    searchParams: params,
  });
}

export async function POST(request) {
  const body = await request.json();
  return forwardToApi("/api/v1/portfolio/global-securities", {
    method: "POST",
    body,
  });
}
