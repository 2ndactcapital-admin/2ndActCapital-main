import { forwardToApi } from "@/lib/apiForward";

// USERMGMT1 — the Access pending page leaves a contact address for the
// admins. The API takes the user from the session's own token; the body
// carries only the address.
export async function PUT(request) {
  const body = await request.json();
  return forwardToApi("/api/v1/users/me/contact-email", {
    method: "PUT",
    body: { contact_email: body?.contact_email ?? "" },
  });
}
