import GroupLanding from "@/components/GroupLanding";

// navregroup — the "controls" master page's landing route. The tabs, their gates
// and the redirect/refusal decision live in lib/menuVisibility.mjs.
export default function Page() {
  return <GroupLanding groupId="controls" />;
}
