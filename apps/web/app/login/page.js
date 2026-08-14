import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { getAuthClientForHost } from "@/lib/authForHost";

export default async function LoginPage() {
  // Resolve the Host so admin.hollisworks.com authenticates against the
  // Hollisworks Auth0 tenant while every other host keeps the EXISTING 2nd Act
  // behavior exactly. The host-aware client selection is unchanged.
  const host = (await headers()).get("host") || "";
  const client = getAuthClientForHost(host);

  // Everyone — including admin.hollisworks.com platform staff — lands in the
  // NORMAL app. A super_admin's role naturally surfaces the admin menu items
  // that already exist; there is no separate admin console.
  const returnTo = "/dashboard";

  const session = await client.getSession();
  if (session) redirect(returnTo);
  redirect(`/auth/login?returnTo=${returnTo}`);
}
