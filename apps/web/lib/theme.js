import { fetchAPI } from "@/lib/api";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/**
 * Sprint 24 — white-label theme.
 *
 * Every colour, font and brand name in the app resolves through here. The
 * values themselves live in `org_settings` (per tenant) and fall back to the
 * DEFAULT_SETTINGS map in apps/api/services/org_settings.py — that backend map
 * is the single place literal brand values are allowed to exist. Nothing in
 * this file, or anywhere else in apps/web, may hardcode a palette value.
 *
 * Settings map onto the `--2a-*` custom properties that globals.css and every
 * component already consume, so theming is a matter of writing the right
 * values into :root at render time.
 */

// setting key -> CSS custom property.
export const COLOR_VARS = {
  "brand.color.navy": "--2a-navy",
  "brand.color.gold": "--2a-gold",
  "brand.color.gold_light": "--2a-gold-light",
  "brand.color.slate_blue": "--2a-nav-rest",
  "brand.color.bg_app": "--2a-bg",
  "brand.color.bg_sidebar": "--2a-bg-sidebar",
  "brand.color.bg_card": "--2a-bg-card",
  "brand.color.text_primary": "--2a-text",
  "brand.color.text_secondary": "--2a-text-secondary",
  "brand.color.text_muted": "--2a-text-muted",
  "brand.color.border": "--2a-border",
};

// Ordered for the admin editors, which group by category.
export const COLOR_KEYS = Object.keys(COLOR_VARS);

export const COLOR_LABELS = {
  "brand.color.navy": "Navy — structure, headings, nav",
  "brand.color.gold": "Gold — accents and hairline rules",
  "brand.color.gold_light": "Gold Light — active state on navy",
  "brand.color.slate_blue": "Slate Blue — resting nav icon",
  "brand.color.bg_app": "App Background",
  "brand.color.bg_sidebar": "Sidebar Background",
  "brand.color.bg_card": "Card Background",
  "brand.color.text_primary": "Text — primary",
  "brand.color.text_secondary": "Text — secondary",
  "brand.color.text_muted": "Text — muted",
  "brand.color.border": "Border",
};

// Fallback stacks are typographic plumbing, not brand values — the tenant
// chooses the family, these just keep it legible while the webfont loads.
const DISPLAY_FALLBACK = "Georgia, serif";
const BODY_FALLBACK = "system-ui, -apple-system, sans-serif";

function quoteFamily(name) {
  return /^[A-Za-z][A-Za-z0-9]*$/.test(name) ? name : `'${name}'`;
}

/** Build the `:root { --2a-*: … }` declarations for a settings object. */
export function themeToCssVars(settings = {}) {
  const decls = [];

  for (const [key, cssVar] of Object.entries(COLOR_VARS)) {
    const value = settings[key];
    if (value) decls.push(`${cssVar}:${value}`);
  }

  const display = settings["brand.font.display"];
  if (display) {
    decls.push(`--2a-font-display:${quoteFamily(display)},${DISPLAY_FALLBACK}`);
  }
  const body = settings["brand.font.body"];
  if (body) {
    decls.push(`--2a-font-body:${quoteFamily(body)},${BODY_FALLBACK}`);
  }

  return decls.join(";");
}

/** The Google Fonts stylesheet URL for the tenant's two families. */
export function fontHref(settings = {}) {
  const families = [];
  const display = settings["brand.font.display"];
  const body = settings["brand.font.body"];
  if (display) {
    families.push(
      `family=${encodeURIComponent(display).replace(/%20/g, "+")}:ital,wght@0,300;0,400;0,500;0,600;1,400`,
    );
  }
  if (body) {
    families.push(
      `family=${encodeURIComponent(body).replace(/%20/g, "+")}:wght@400;500;600;700`,
    );
  }
  if (!families.length) return null;
  return `https://fonts.googleapis.com/css2?${families.join("&")}&display=swap`;
}

const EMPTY_THEME = { org_id: null, org_name: null, org_slug: null, settings: {} };

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

/** Convenience readers so callers never index the settings map by hand. */
export function brandName(settings = {}) {
  return settings["brand.name"] || "";
}

export function brandShortName(settings = {}) {
  return settings["brand.short_name"] || brandName(settings);
}

export function logoUrl(settings = {}) {
  return settings["brand.logo_url"] || null;
}

export function faviconUrl(settings = {}) {
  return settings["brand.favicon_url"] || null;
}
