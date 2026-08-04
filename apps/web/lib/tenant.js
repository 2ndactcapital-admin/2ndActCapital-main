import { headers } from "next/headers";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/**
 * Hollisworks multi-tenant resolution (Sprint 1 correction wiring).
 *
 * The platform apex — hollisworks.com / www.hollisworks.com — is the
 * Hollisworks *marketing* site, not a tenant. Each client firm (2nd Act
 * included) is reached only via its own `<slug>.hollisworks.com` subdomain.
 *
 * This runs server-side, so we read the *browser's* Host header off the incoming
 * request and forward it to the pre-auth resolver (`GET /api/v1/tenant/resolve`)
 * as `?host=` — a plain server->API fetch would otherwise carry the API's own
 * host. The resolver is the single source of truth for the marketing-vs-tenant
 * decision; we do not duplicate the subdomain logic here.
 *
 * Returns the resolver dict: `{ marketing, resolved, org_id, org_slug,
 * org_name, subdomain, source }`. Never throws — if the API is unreachable we
 * default to `marketing: false` so an existing tenant (2nd Act) still renders
 * its app rather than being replaced by the marketing page on a transient error.
 */
export async function resolveTenant() {
  let host = "";
  try {
    const h = await headers();
    host = h.get("host") || "";
  } catch {
    // headers() unavailable (e.g. static context) — fall through to default.
  }

  const fallback = {
    marketing: false,
    resolved: false,
    org_id: null,
    org_slug: null,
    org_name: null,
    subdomain: null,
    source: "default",
  };

  try {
    const url = new URL(`${API_BASE}/api/v1/tenant/resolve`);
    if (host) url.searchParams.set("host", host);
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) return fallback;
    const data = await res.json();
    // Defensive: guarantee the marketing flag is a boolean the caller can trust.
    return { ...fallback, ...data, marketing: Boolean(data.marketing) };
  } catch {
    return fallback;
  }
}
