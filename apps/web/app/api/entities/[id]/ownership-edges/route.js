import { forwardToApi } from "@/lib/apiForward";

// PORTENTRY2 (MUSE-OLIVO-F-02) — the CRM record's flat {nodes, edges} ownership read (both ownership tables), the
// SAME payload the Overview renders; the Ownership tab re-reads it after every write. (/ownership-graph on this side
// forwards to the staff focal tree — a different shape.)
export async function GET(_request, { params }) {
  const { id } = await params;
  return forwardToApi(`/api/v1/entities/${encodeURIComponent(id)}/ownership-graph`);
}
