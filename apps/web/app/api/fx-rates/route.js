import { forwardToApi } from "@/lib/apiForward";

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const sp = {};
  if (searchParams.get("base")) sp.base = searchParams.get("base");
  if (searchParams.get("quote")) sp.quote = searchParams.get("quote");
  if (searchParams.get("as_of")) sp.as_of = searchParams.get("as_of");
  return forwardToApi("/api/v1/fx-rates", {
    searchParams: Object.keys(sp).length ? sp : undefined,
  });
}
