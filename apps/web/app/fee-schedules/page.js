import { redirect } from "next/navigation";

// BUGFIX1 — /fee-schedules had no index page (404); the only page under it is
// the fee schedule assistant, which keeps its own host-aware session check.
export default function FeeSchedulesIndex() {
  redirect("/fee-schedules/chat");
}
