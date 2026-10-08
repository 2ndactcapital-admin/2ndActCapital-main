import { getRequestAuthClient } from "@/lib/authServer";
import { createMarketHandler } from "@/lib/market/marketRoutes.mjs";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// Route handler for one market data route (mkt04a). HOST-AWARE: the session and
// token are read with the Auth0 client belonging to THIS request's tenant,
// through the same getRequestAuthClient every other forward uses. The forward
// logic itself lives in lib/market/marketRoutes.mjs so it can be tested with a
// faked session and backend. Never call FastAPI from the client (CLAUDE.md
// Rule 5).
export function marketHandler(id) {
  return createMarketHandler(id, {
    getAuthClient: getRequestAuthClient,
    fetch: (url, init) => fetch(url, init),
    apiBase: API_BASE,
  });
}
