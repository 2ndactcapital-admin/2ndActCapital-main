import FirmSearchClient from "@/components/FirmSearchClient";

// Shared Login/Enroll interstitial for the Hollisworks marketing surface. Both
// the "Log in" and "Enroll" buttons route here; the ?intent= param remembers
// which was clicked so the eventual redirect targets the firm's login vs enroll
// URL. Served on the platform apex (bare hollisworks.com) alongside the
// marketing page.
export const metadata = {
  title: "Find your firm — Hollisworks",
};

export default async function FirmSearchPage({ searchParams }) {
  const params = (await searchParams) || {};
  const raw = Array.isArray(params.intent) ? params.intent[0] : params.intent;
  // Normalize to the two valid intents; anything else defaults to login.
  const intent = raw === "enroll" ? "enroll" : "login";
  return <FirmSearchClient intent={intent} />;
}
