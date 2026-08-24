import { redirect, notFound } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { isStaff } from "@/lib/roles";
import SPVLedgerClient from "@/components/spv/SPVLedgerClient";

export default async function LedgerPage({ params }) {
  const { id } = await params;

  const session = await getHostSession();
  if (!session) redirect(`/auth/login?returnTo=/spvs/${id}/ledger`);
  if (!isStaff(session.user)) redirect(`/spvs/${id}`);

  return (
    <AppShell user={session.user}>
      <SPVLedgerClient vehicleId={id} />
    </AppShell>
  );
}
