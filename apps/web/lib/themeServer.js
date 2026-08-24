import { fetchAPI } from "@/lib/api";
import { EMPTY_THEME } from "@/lib/theme";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/**
 * SERVER-ONLY half of the theme module.
 *
 * `lib/theme.js` holds the pure readers (brandName, logoUrl, COLOR_VARS, …) and
 * is imported by client components. `loadTheme` is the one export that needs
 * the caller's Auth0 token, so it lives here: a static `@/lib/api` import from
 * the shared module pulled the server-only auth chain — now including
 * `next/headers`, since the token became host-aware — into the client bundle.
 */

/**
 * Server-side theme load for the root layout.
 *
 * Tries the authenticated endpoint first (the caller's own org), then falls
 * back to the public one so the login screen is still branded. Never throws —
 * an unreachable API yields an unstyled-but-working shell rather than a 500.
 */
export async function loadTheme() {
  try {
    // cache: "no-store" is required here: this call carries the caller's role,
    // and a cached response served a pre-promotion role to /admin/platform (a
    // freshly-promoted super_admin saw the restricted view). Kept explicit so
    // it survives independent of fetchAPI's default — matching the public
    // fallback fetch below.
    return await fetchAPI("/api/v1/theme", { cache: "no-store" });
  } catch {
    // Not signed in, or the API is unreachable — fall through to public.
  }

  try {
    const res = await fetch(`${API_BASE}/api/v1/theme/public`, {
      cache: "no-store",
    });
    if (res.ok) return await res.json();
  } catch {
    // API down.
  }

  return EMPTY_THEME;
}
