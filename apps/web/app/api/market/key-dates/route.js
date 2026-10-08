// mkt04a — thin forward to the market data API. Auth, identity and the
// pass-through rules live in lib/market/marketRoutes.mjs (via lib/marketForward).
import { marketHandler } from "@/lib/marketForward";

export const GET = marketHandler("keyDates");
