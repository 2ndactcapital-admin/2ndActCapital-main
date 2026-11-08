import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import DisclosuresAdmin from "@/components/disclosures/Disclosures";

// DISCLOSURES1 — Admin → Disclosures: one library for the whole platform. Every key, what is in force for this org
// and why; per key its versions (each a file in R2 + its summary); upload a draft → a second person publishes it.
export default async function DisclosuresPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/admin/disclosures");
  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI("/api/v1/disclosures");
  } catch (e) {
    error = e.status === 403 ? "The disclosure library is open to staff holding view_disclosures." : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="text-3xl font-semibold text-navy">{labelFor("/admin/disclosures")}</h1>
      <div className="mt-6">
        {error ? <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
          : <DisclosuresAdmin initialEnvelope={envelope} />}
      </div>
    </AppShell>
  );
}
