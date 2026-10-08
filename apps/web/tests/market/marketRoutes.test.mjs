/**
 * The market route handlers (mkt04a Task 2), exercised through the SAME
 * createMarketHandler every app/api/market/** route exports (via
 * lib/marketForward.js), with a faked Auth0 client and a faked backend.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  MARKET_ROUTES,
  NO_TOKEN,
  UNAUTHORIZED,
  createMarketHandler,
} from "../../lib/market/marketRoutes.mjs";

const API = "http://backend.test";
const TOKEN = "tok-123";

function fakeAuth({ session = { user: { sub: "auth0|x" } }, token = TOKEN } = {}) {
  return {
    async getSession() {
      return session;
    },
    async getAccessToken() {
      return token ? { token } : {};
    },
  };
}

function fakeBackend(status = 200, text = '{"ok":true}', contentType = "application/json") {
  const calls = [];
  const fetch = async (url, init) => {
    calls.push({ url, init });
    return new Response(text, { status, headers: { "content-type": contentType } });
  };
  return { calls, fetch };
}

function handlerFor(route, { auth = fakeAuth(), backend = fakeBackend() } = {}) {
  return createMarketHandler(route.id, { getAuthClient: async () => auth, fetch: backend.fetch, apiBase: API });
}

function requestFor(route, { search = "", body } = {}) {
  const url = `http://web.test${route.web.replace("[id]", "row-1")}${search}`;
  return new Request(url, {
    method: route.method,
    body: route.body ? (body ?? '{"name":"x"}') : undefined,
    headers: route.body ? { "content-type": "application/json" } : undefined,
  });
}

const ctxFor = (route) => (route.param ? { params: Promise.resolve({ [route.param]: "row-1" }) } : undefined);

test("there are exactly eleven routes, one per backend route in the API contract", () => {
  const expected = [
    "GET /market/catalog", "GET /market/series", "POST /market/grid", "POST /market/correlations",
    "GET /market/key-dates", "POST /market/key-dates/custom", "DELETE /market/key-dates/custom/{id}",
    "GET /market/views", "POST /market/views", "PUT /market/views/{id}", "DELETE /market/views/{id}",
  ];
  assert.deepEqual(MARKET_ROUTES.map((r) => `${r.method} ${r.backend}`).sort(), expected.sort());
});

for (const route of MARKET_ROUTES) {
  test(`${route.method} ${route.web}: no session -> 401 Unauthorized, backend never called`, async () => {
    const backend = fakeBackend();
    const res = await handlerFor(route, { auth: fakeAuth({ session: null }), backend })(requestFor(route), ctxFor(route));
    assert.equal(res.status, 401);
    assert.deepEqual(await res.json(), UNAUTHORIZED);
    assert.equal(res.headers.get("cache-control"), "no-store");
    assert.equal(backend.calls.length, 0);
  });

  test(`${route.method} ${route.web}: session but no token -> 401, backend never called`, async () => {
    const backend = fakeBackend();
    const res = await handlerFor(route, { auth: fakeAuth({ token: null }), backend })(requestFor(route), ctxFor(route));
    assert.equal(res.status, 401);
    assert.deepEqual(await res.json(), NO_TOKEN);
    assert.equal(backend.calls.length, 0);
  });

  test(`${route.method} ${route.web}: forwards to ${route.method} /api/v1${route.backend} with the bearer token`, async () => {
    const backend = fakeBackend();
    const body = '{"keys":["a"],  "anchor":"2020-01-01"}';
    const res = await handlerFor(route, { backend })(
      requestFor(route, { search: "?keys=a,b&from=2020-01-01", body }),
      ctxFor(route),
    );
    assert.equal(res.status, 200);
    assert.equal(backend.calls.length, 1);
    const { url, init } = backend.calls[0];
    const expectedPath = `/api/v1${route.backend.replace("{id}", "row-1")}`;
    const u = new URL(url);
    assert.equal(u.origin, API);
    assert.equal(u.pathname, expectedPath);
    assert.equal(u.search, route.query ? "?keys=a,b&from=2020-01-01" : "");
    assert.equal(init.method, route.method);
    assert.equal(init.headers.Authorization, `Bearer ${TOKEN}`);
    assert.equal(init.cache, "no-store");
    if (route.body) {
      // The caller's own text, unchanged — not re-serialised, nothing added.
      assert.equal(init.body, body);
    } else {
      assert.equal(init.body, undefined);
    }
    assert.equal(res.headers.get("cache-control"), "no-store");
    // Identity is never a URL or body field.
    assert.ok(!/org_id|user_id/.test(url));
    assert.ok(!/org_id|user_id/.test(init.body ?? ""));
  });

  for (const [status, text] of [
    [401, '{"detail":"Authentication required"}'],
    [403, '{"detail":"Permission required: view_market"}'],
    [409, '{"detail":{"message":"You already have a view with that name."}}'],
    [422, '{"detail":{"message":"Request validation failed","errors":[{"loc":["body","anchor"],"type":"window","msg":"anchor must not be after end"}]}}'],
  ]) {
    test(`${route.method} ${route.web}: backend ${status} passes through with its exact body`, async () => {
      const backend = fakeBackend(status, text);
      const res = await handlerFor(route, { backend })(requestFor(route), ctxFor(route));
      assert.equal(res.status, status);
      assert.equal(await res.text(), text);
      assert.equal(res.headers.get("cache-control"), "no-store");
    });
  }
}

test("a body that is not JSON is refused before the backend is called", async () => {
  for (const route of MARKET_ROUTES.filter((r) => r.body)) {
    const backend = fakeBackend();
    const res = await handlerFor(route, { backend })(requestFor(route, { body: "{not json" }), ctxFor(route));
    assert.equal(res.status, 400);
    assert.equal(backend.calls.length, 0);
  }
});

test("a body carrying org_id is forwarded unchanged so the API refuses it (never stripped, never added)", async () => {
  const route = MARKET_ROUTES.find((r) => r.id === "grid");
  const backend = fakeBackend(422, '{"detail":{"message":"Request validation failed"}}');
  const body = '{"keys":["a"],"anchor":"2020-01-01","org_id":"x"}';
  const res = await handlerFor(route, { backend })(requestFor(route, { body }), undefined);
  assert.equal(backend.calls[0].init.body, body);
  assert.equal(res.status, 422);
});

test("a path id is URL-encoded, never interpreted", async () => {
  const route = MARKET_ROUTES.find((r) => r.id === "viewDelete");
  const backend = fakeBackend();
  await handlerFor(route, { backend })(requestFor(route), { params: Promise.resolve({ id: "../catalog?x=1" }) });
  assert.equal(new URL(backend.calls[0].url).pathname, "/api/v1/market/views/..%2Fcatalog%3Fx%3D1");
});

test("an unreachable backend is a 502, not a 200", async () => {
  const route = MARKET_ROUTES.find((r) => r.id === "catalog");
  const res = await createMarketHandler(route.id, {
    getAuthClient: async () => fakeAuth(),
    fetch: async () => {
      throw new Error("connect ECONNREFUSED");
    },
    apiBase: API,
  })(requestFor(route));
  assert.equal(res.status, 502);
});
