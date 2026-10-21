import FirmSearchClient from "@/components/FirmSearchClient";
import { firmSearchBrand } from "@/lib/firmSearchBrand";

// Shared Login/Enroll interstitial for the Hollisworks marketing surface. Both
// the "Log in" and "Enroll" buttons route here; the ?intent= param remembers
// which was clicked so the eventual redirect targets the firm's login vs enroll
// URL. Served on the platform apex (bare hollisworks.com) alongside the
// marketing page.
// BUGFIX1 (FIRM-01) — the title and masthead carry the HOST's brand (org
// branding settings), not a hard-coded "Hollisworks".
export async function generateMetadata() {
  const brand = await firmSearchBrand();
  return { title: brand.name ? `Find your firm — ${brand.name}` : "Find your firm" };
}

export default async function FirmSearchPage({ searchParams }) {
  const params = (await searchParams) || {};
  const raw = Array.isArray(params.intent) ? params.intent[0] : params.intent;
  // Normalize to the two valid intents; anything else defaults to login.
  const intent = raw === "enroll" ? "enroll" : "login";
  const brand = await firmSearchBrand();
  return <FirmSearchClient intent={intent} brand={brand.name} supportEmail={brand.supportEmail} />;
}
