import { redirect } from "next/navigation";

// BUGFIX1 — Semantic Search lives at /documents/semantic. Old links keep working.
export default function SemanticSearchMoved() {
  redirect("/documents/semantic");
}
