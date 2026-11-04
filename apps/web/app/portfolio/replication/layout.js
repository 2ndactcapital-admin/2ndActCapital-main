import AreaGuard from "@/components/AreaGuard";

// BUGFIX2 — every page under /portfolio/replication is refused server-side to a caller the area's
// gate (lib/menuVisibility.mjs AREA_GATES["/portfolio/replication"]) does not admit.
export default function Layout({ children }) {
  return <AreaGuard area="/portfolio/replication">{children}</AreaGuard>;
}
