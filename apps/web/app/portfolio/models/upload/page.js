import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import UploadWizard from "@/components/models/UploadWizard";

// REP3 — the upload wizard. The models envelope supplies the caller's flags
// and the field / validation vocabularies; ?model=<id> versions an existing
// upload model instead of creating a new one.
export default async function ModelUploadPage({ searchParams }) {
  const sp = await searchParams;
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/portfolio/models/upload");

  let envelope = null;
  let target = null;
  let error = null;
  try {
    envelope = await fetchAPI("/api/v1/models");
    if (sp?.model) target = (await fetchAPI(`/api/v1/models/${encodeURIComponent(sp.model)}`))?.model || null;
  } catch (e) {
    error = e.status === 403 ? "Models are open to staff with model access." : e.message;
  }
  const canWrite = envelope?.permissions?.can_write === true;
  return (
    <AppShell user={session.user}>
      <p className="text-sm"><a className="text-navy underline" href="/portfolio/models">Models</a></p>
      <h1 className="mt-1 text-3xl font-semibold text-navy">Upload a model</h1>
      <p className="mt-1 text-sm text-text-muted">
        Templates: <a className="underline" href="/api/models/template?format=xlsx">.xlsx</a> ·{" "}
        <a className="underline" href="/api/models/template?format=csv">.csv</a>
      </p>
      <div className="mt-6">
        {error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
        ) : canWrite ? (
          <UploadWizard envelope={envelope} targetModel={target} />
        ) : (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Uploading a model needs model management access.
          </div>
        )}
      </div>
    </AppShell>
  );
}
