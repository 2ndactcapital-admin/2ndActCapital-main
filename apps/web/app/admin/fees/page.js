import { redirect } from "next/navigation";

// PERMS1 (WF-04) — /admin/fees was a dead 404. The fee screens live in the
// Billing & Custody group; its landing sends the caller to the first tab their
// permissions allow (or says plainly that none is open to them).
export default function AdminFeesPage() {
  redirect("/admin/billing");
}
