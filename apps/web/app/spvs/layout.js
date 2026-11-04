import AreaGuard from "@/components/AreaGuard";

// BUGFIX2 — every page under /spvs is refused server-side to a caller the area's
// gate (lib/menuVisibility.mjs AREA_GATES["/spvs"]) does not admit.
export default function Layout({ children }) {
  return <AreaGuard area="/spvs">{children}</AreaGuard>;
}
