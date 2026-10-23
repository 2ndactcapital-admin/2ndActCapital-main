import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import UploadLog from "@/components/models/UploadLog";

// REP3 — the permanent upload log (every outcome), exportable for compliance.
export default async function ModelUploadLogPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/portfolio/models/uploads");

  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI("/api/v1/models/uploads");
  } catch (e) {
    error = e.status === 403 ? "The upload log is open to staff with model access." : e.message;
  }
  return (
    <AppShell user={session.user}>
      <p className="text-sm"><a className="text-navy underline" href="/portfolio/models">Models</a></p>
      <h1 className="mt-1 text-3xl font-semibold text-navy">Upload log</h1>
      <p className="mt-1 text-sm text-text-muted">Every file received — approved, rejected, abandoned, duplicate or failed. Nothing is ever removed.</p>
      <div className="mt-6">
        {error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
        ) : (
          <UploadLog initialEnvelope={envelope} />
        )}
      </div>
    </AppShell>
  );
}
