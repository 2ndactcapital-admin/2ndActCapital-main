import { redirect } from "next/navigation";

// BUGFIX1 — Document Search lives at /documents (the Documents group), not
// under /admin. Old links keep working.
export default function DocumentSearchMoved() {
  redirect("/documents");
}
