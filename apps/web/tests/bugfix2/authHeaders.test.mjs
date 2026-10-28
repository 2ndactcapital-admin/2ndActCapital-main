// BUGFIX2 B4 — expired sign-in session. lib/api.js's authHeaders() used to
// swallow a failing getAccessToken() and return {}, so server-rendered pages
// showed the API's "Missing or malformed Authorization header". It now hands
// the failure to bearerHeaders' onExpired, which in lib/api.js is
// redirect(LOGIN_PATH).
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { bearerHeaders, LOGIN_PATH } from "../../lib/authHeaders.mjs";
// Next's package has no "exports" map for plain Node ESM — import the .js file.
import { redirect } from "next/navigation.js";

const throwingClient = (err) => ({
  getAccessToken: async () => {
    throw err;
  },
});
const tokenClient = (result) => ({ getAccessToken: async () => result });

test("LOGIN_PATH is the Auth0 v4 login route the app already uses", () => {
  assert.equal(LOGIN_PATH, "/auth/login");
});

test("(1) getAccessToken throws -> onExpired gets the error; its return and its throw both propagate", async () => {
  const err = new Error("The access token has expired and a refresh token was not provided.");
  let seen;
  const out = await bearerHeaders(throwingClient(err), (e) => {
    seen = e;
    return { sentinel: true };
  });
  assert.equal(seen, err);
  assert.deepEqual(out, { sentinel: true });

  const thrown = new Error("from onExpired");
  await assert.rejects(
    bearerHeaders(throwingClient(err), () => {
      throw thrown;
    }),
    (e) => e === thrown,
  );
});

test("(2) the REAL next/navigation redirect as onExpired -> rejects with NEXT_REDIRECT to the login path", async () => {
  await assert.rejects(
    bearerHeaders(throwingClient(new Error("expired")), () => redirect(LOGIN_PATH)),
    (e) => {
      assert.equal(typeof e.digest, "string");
      assert.ok(e.digest.startsWith("NEXT_REDIRECT"), e.digest);
      assert.ok(e.digest.includes(LOGIN_PATH), e.digest);
      return true;
    },
  );
});

test("(3) a token -> the Bearer header (token or accessToken)", async () => {
  const never = () => assert.fail("onExpired must not run when a token is returned");
  assert.deepEqual(await bearerHeaders(tokenClient({ token: "abc" }), never), {
    Authorization: "Bearer abc",
  });
  assert.deepEqual(await bearerHeaders(tokenClient({ accessToken: "xyz" }), never), {
    Authorization: "Bearer xyz",
  });
});

test("(4) no token and no throw -> {} (unchanged behaviour)", async () => {
  const never = () => assert.fail("onExpired must not run without a failure");
  assert.deepEqual(await bearerHeaders(tokenClient(undefined), never), {});
  assert.deepEqual(await bearerHeaders(tokenClient({}), never), {});
  assert.deepEqual(await bearerHeaders(tokenClient({ token: "" }), never), {});
});

test("(5) lib/api.js authHeaders delegates to bearerHeaders with redirect(LOGIN_PATH); the silent catch is gone", async () => {
  const src = await readFile(new URL("../../lib/api.js", import.meta.url), "utf8");
  const start = src.indexOf("async function authHeaders()");
  assert.ok(start >= 0, "authHeaders() not found in lib/api.js");
  const end = src.indexOf("\n}\n", start);
  const body = src.slice(start, end);
  assert.match(body, /return bearerHeaders\(authClient, \(\) => redirect\(LOGIN_PATH\)\)/);
  assert.doesNotMatch(body, /catch\s*\{/);
  assert.doesNotMatch(body, /\.getAccessToken\(/);
  // The client is still resolved outside any try (fail-loud on misconfiguration).
  assert.doesNotMatch(body, /\btry\b/);
  assert.match(src, /import \{ redirect \} from "next\/navigation";/);
  assert.match(src, /import \{ bearerHeaders, LOGIN_PATH \} from "@\/lib\/authHeaders\.mjs";/);
  // No other getAccessToken call with a silent catch survives elsewhere in the file.
  assert.doesNotMatch(src, /getAccessToken\(\)[\s\S]{0,200}catch\s*\{/);
});
