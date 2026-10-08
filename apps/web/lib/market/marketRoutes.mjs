/**
 * The Next.js -> FastAPI forward for the market data API (mkt04a). Pure.
 *
 * This module imports NOTHING (no React, no "@/..." aliases, no next/*), so the
 * EXACT handler every app/api/market/** route exports can be exercised by a
 * plain Node test with a faked session and a faked backend
 * (tests/market/marketRoutes.test.mjs). Same discipline as lib/menuVisibility.mjs:
 * a forward that is re-implemented inside a test proves only that the test
 * agrees with itself.
 *
 * lib/marketForward.js binds it to the real, host-aware Auth0 client
 * (lib/authServer getRequestAuthClient) and the real fetch.
 *
 * Mirrors lib/apiForward.js (session check, token, same unauthenticated
 * responses, Bearer header, no-store fetch) with four deliberate differences,
 * each required by mkt04a:
 *   1. The backend's status AND body are passed back byte-for-byte. apiForward
 *      rewrites an error into {error: detail}; the market API's refusals carry
 *      a structured detail ({message, errors[], unknown_keys…}) the page shows.
 *   2. Every response carries Cache-Control: no-store (views and custom dates
 *      are per-user data).
 *   3. Nothing is logged. apiForward logs the backend's error body.
 *   4. A request body is forwarded as the caller's own text after confirming
 *      it parses as JSON — never re-serialised, never added to.
 *
 * Identity travels ONLY in the Authorization header the server attaches. No
 * code here reads or writes an org or user field, in a URL or a body.
 */

export const API_PREFIX = "/api/v1";

/**
 * Every market route: the Next.js path the browser calls, the HTTP method, the
 * backend path, whether the browser's query string is passed through, and
 * whether a JSON body is forwarded. `param` names the dynamic path segment.
 */
export const MARKET_ROUTES = [
  { id: "catalog", method: "GET", web: "/api/market/catalog", backend: "/market/catalog" },
  { id: "series", method: "GET", web: "/api/market/series", backend: "/market/series", query: true },
  { id: "grid", method: "POST", web: "/api/market/grid", backend: "/market/grid", body: true },
  { id: "correlations", method: "POST", web: "/api/market/correlations", backend: "/market/correlations", body: true },
  { id: "keyDates", method: "GET", web: "/api/market/key-dates", backend: "/market/key-dates" },
  { id: "customDateCreate", method: "POST", web: "/api/market/key-dates/custom", backend: "/market/key-dates/custom", body: true },
  { id: "customDateDelete", method: "DELETE", web: "/api/market/key-dates/custom/[id]", backend: "/market/key-dates/custom/{id}", param: "id" },
  { id: "views", method: "GET", web: "/api/market/views", backend: "/market/views" },
  { id: "viewCreate", method: "POST", web: "/api/market/views", backend: "/market/views", body: true },
  { id: "viewUpdate", method: "PUT", web: "/api/market/views/[id]", backend: "/market/views/{id}", param: "id", body: true },
  { id: "viewDelete", method: "DELETE", web: "/api/market/views/[id]", backend: "/market/views/{id}", param: "id" },
];

const NO_STORE = "no-store";

function json(payload, status) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": NO_STORE },
  });
}

// The same two unauthenticated responses lib/apiForward.js returns.
export const UNAUTHORIZED = { error: "Unauthorized" };
export const NO_TOKEN = { error: "Not authenticated — please log out and log back in." };
export const BAD_JSON = { error: "The request body is not valid JSON." };
export const UNREACHABLE = { error: "The market data service could not be reached." };

export function routeById(id) {
  const route = MARKET_ROUTES.find((r) => r.id === id);
  if (!route) throw new Error(`Unknown market route: ${id}`);
  return route;
}

/** The backend URL for one call. Path params are encoded; nothing is added. */
export function backendUrl(route, apiBase, { paramValue, search } = {}) {
  let path = route.backend;
  if (route.param) {
    path = path.replace(`{${route.param}}`, encodeURIComponent(String(paramValue ?? "")));
  }
  const qs = route.query && search ? (search.startsWith("?") ? search : `?${search}`) : "";
  return `${apiBase}${API_PREFIX}${path}${qs}`;
}

/**
 * Build the route handler for one market route.
 *
 * deps: { getAuthClient: async () => Auth0 client, fetch, apiBase }
 * Returns `async (request, context) => Response`.
 */
export function createMarketHandler(id, deps) {
  const route = routeById(id);
  return async function marketHandler(request, context) {
    const authClient = await deps.getAuthClient();

    let session;
    try {
      session = await authClient.getSession();
    } catch {
      // ignore — treated as no session, exactly as lib/apiForward.js does
    }
    if (!session) return json(UNAUTHORIZED, 401);

    let token;
    try {
      const result = await authClient.getAccessToken();
      token = result?.token || result?.accessToken;
    } catch {
      // no logging: the error may carry token material
    }
    if (!token) return json(NO_TOKEN, 401);

    let bodyText;
    if (route.body) {
      bodyText = await request.text();
      try {
        JSON.parse(bodyText);
      } catch {
        return json(BAD_JSON, 400);
      }
    }

    let paramValue;
    if (route.param) {
      const params = await context?.params;
      paramValue = params?.[route.param];
    }
    const search = route.query ? new URL(request.url).search : "";
    const url = backendUrl(route, deps.apiBase, { paramValue, search });

    let res;
    try {
      res = await deps.fetch(url, {
        method: route.method,
        headers: {
          ...(route.body ? { "Content-Type": "application/json" } : {}),
          Authorization: `Bearer ${token}`,
        },
        body: route.body ? bodyText : undefined,
        cache: NO_STORE,
      });
    } catch {
      return json(UNREACHABLE, 502);
    }

    // Status and body unchanged: a backend 401/403/409/422 reaches the page as
    // itself, never as a 200.
    const text = await res.text();
    return new Response(text, {
      status: res.status,
      headers: {
        "Content-Type": res.headers.get("content-type") || "application/json",
        "Cache-Control": NO_STORE,
      },
    });
  };
}
