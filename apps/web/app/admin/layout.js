import AreaGuard from "@/components/AreaGuard";

// BUGFIX2 — every page under /admin is refused server-side to a caller the area's
// gate (lib/menuVisibility.mjs AREA_GATES["/admin"]) does not admit.
export default function Layout({ children }) {
  return <AreaGuard area="/admin">{children}</AreaGuard>;
}
