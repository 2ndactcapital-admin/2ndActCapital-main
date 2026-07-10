import { NextResponse } from "next/server";
import { forwardToApi } from "@/lib/apiForward";

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const as_of = searchParams.get("as_of");
  return forwardToApi("/api/v1/ledger/accounts", {
    searchParams: as_of ? { as_of } : undefined,
  });
}

export async function POST(request) {
  const body = await request.json();
  return forwardToApi("/api/v1/ledger/accounts", { method: "POST", body });
}
