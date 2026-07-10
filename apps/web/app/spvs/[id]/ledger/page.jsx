import { redirect, notFound } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import { isStaff } from "@/lib/roles";
import SPVLedgerClient from "@/components/spv/SPVLedgerClient";

export default async function LedgerPage({ params }) {
  const { id } = await params;

  const session = await auth0.getSession();
  if (!session) redirect(`/auth/login?returnTo=/spvs/${id}/ledger`);
  if (!isStaff(session.user)) redirect(`/spvs/${id}`);

  return (
    <AppShell user={session.user}>
      <SPVLedgerClient vehicleId={id} />
    </AppShell>
  );
}
