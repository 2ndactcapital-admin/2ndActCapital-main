import GroupLanding from "@/components/GroupLanding";

// navregroup — the "ai_models" master page's landing route. The tabs, their gates
// and the redirect/refusal decision live in lib/menuVisibility.mjs.
export default function Page() {
  return <GroupLanding groupId="ai_models" />;
}
