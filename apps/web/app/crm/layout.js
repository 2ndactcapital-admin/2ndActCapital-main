import AreaGuard from "@/components/AreaGuard";

// BUGFIX2 — every page under /crm is refused server-side to a caller the area's
// gate (lib/menuVisibility.mjs AREA_GATES["/crm"]) does not admit.
export default function Layout({ children }) {
  return <AreaGuard area="/crm">{children}</AreaGuard>;
}
