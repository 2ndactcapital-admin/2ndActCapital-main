import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { AccountLongShort } from "@/components/replication/LongShortPanel";

// REP13 — the account's Long/short panel: eligibility (each piece), the margin agreement on the member's
// suitability profile, the settings and their history, the short book. The envelope is fetched server-side.
export default async function AccountLongShortPage({ params }) {
  const { id } = await params;
  const session = await getHostSession();
  if (!session) redirect(`/auth/login?returnTo=/portfolio/accounts/${id}/long-short`);

  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI(`/api/v1/long-short/accounts/${encodeURIComponent(id)}`);
  } catch (e) {
    error = e.status === 404 ? "This account was not found." : e.status === 403
      ? "Long/short is open to staff with proposal access." : e.message;
  }
  return (
    <AppShell user={session.user}>
      <p className="text-sm"><a className="text-navy underline" href={`/portfolio/accounts/${id}`}>Account</a></p>
      <h1 className="mt-1 font-serif text-3xl font-semibold text-navy">Long/short</h1>
      <div className="mt-4">
        {error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
        ) : (
          <AccountLongShort accountId={id} initialEnvelope={envelope} />
        )}
      </div>
    </AppShell>
  );
}
