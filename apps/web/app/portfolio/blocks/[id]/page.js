import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import BlockDetail from "@/components/blocks/BlockDetail";

// REP9 — one trade block. The envelope (the server's flags, its actions list
// for this caller and the approval task) is fetched server-side.
export default async function BlockPage({ params }) {
  const { id } = await params;
  const session = await getHostSession();
  if (!session) redirect(`/auth/login?returnTo=/portfolio/blocks/${id}`);

  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI(`/api/v1/execution/blocks/${encodeURIComponent(id)}`);
  } catch (e) {
    error = e.status === 404 ? "This block was not found." : e.status === 403
      ? "Blocks are open to staff with block-trading access." : e.message;
  }
  return (
    <AppShell user={session.user}>
      <p className="text-sm"><a className="text-navy underline" href="/portfolio/blocks">Blocks</a></p>
      <h1 className="mt-1 text-3xl font-semibold text-navy">Block</h1>
      <div className="mt-4">
        {error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
        ) : (
          <BlockDetail initialEnvelope={envelope} />
        )}
      </div>
    </AppShell>
  );
}
