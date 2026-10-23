import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import ModelPage from "@/components/models/ModelPage";

// REP3 — one model. The envelope (the server's flags, its actions for this
// caller, the live and open versions with their own actions) is fetched here.
export default async function ModelDetailPage({ params }) {
  const { id } = await params;
  const session = await getHostSession();
  if (!session) redirect(`/auth/login?returnTo=/portfolio/models/${id}`);

  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI(`/api/v1/models/${encodeURIComponent(id)}`);
  } catch (e) {
    error = e.status === 404 ? "This model was not found." : e.status === 403
      ? "Models are open to staff with model access." : e.message;
  }
  return (
    <AppShell user={session.user}>
      <p className="text-sm"><a className="text-navy underline" href="/portfolio/models">Models</a></p>
      <div className="mt-2">
        {error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
        ) : (
          <ModelPage initialEnvelope={envelope} />
        )}
      </div>
    </AppShell>
  );
}
