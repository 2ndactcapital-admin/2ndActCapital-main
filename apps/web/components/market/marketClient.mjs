/**
 * The one browser request helper for mkt04c's panels. Callers pass a URL built
 * by a lib/market module from its route constant (/api/market/*, the Next.js
 * forwards); identity travels only in the session the forward reads, never
 * here. A network failure resolves to {ok: false, status: 0, body: null}, so
 * every caller goes through its own fail-closed interpreter.
 */
export async function requestJson(url, { method = "GET", body } = {}) {
  try {
    const res = await fetch(url, {
      method,
      cache: "no-store",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const parsed = await res.json().catch(() => null);
    return { ok: res.ok, status: res.status, body: parsed };
  } catch {
    return { ok: false, status: 0, body: null };
  }
}
