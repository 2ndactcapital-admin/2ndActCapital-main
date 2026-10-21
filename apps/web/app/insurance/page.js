import { renderComingSoon } from "@/lib/comingSoonPage";

// BUGFIX1 — flagged comingSoon in lib/menuVisibility.mjs; no page is built yet.
export default async function Page() {
  return renderComingSoon("/insurance");
}
