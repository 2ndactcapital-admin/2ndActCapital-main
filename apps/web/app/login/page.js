import { headers } from "next/headers";
import { redirect } from "next/navigation";
import {
  getAuthClientForHost,
  isHollisworksAdminHost,
} from "@/lib/authForHost";

export default async function LoginPage() {
  // Resolve the Host so admin.hollisworks.com logs in against the Hollisworks
  // tenant while every other host keeps the EXISTING 2nd Act behavior exactly.
  const host = (await headers()).get("host") || "";
  const client = getAuthClientForHost(host);
  const isAdmin = isHollisworksAdminHost(host);

  // Where a signed-in user lands: the platform-staff console on the admin host,
  // the member dashboard everywhere else (unchanged 2nd Act path).
  const returnTo = isAdmin ? "/admin-console" : "/dashboard";

  const session = await client.getSession();
  if (session) redirect(returnTo);
  redirect(`/auth/login?returnTo=${returnTo}`);
}
