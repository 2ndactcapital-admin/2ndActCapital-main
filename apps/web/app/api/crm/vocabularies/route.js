import { forwardToApi } from "@/lib/apiForward";

// CRMDETAIL1 — every CRM vocabulary (relationship types, detail fields, liability kinds).
export async function GET() {
  return forwardToApi(`/api/v1/crm/vocabularies`);
}
