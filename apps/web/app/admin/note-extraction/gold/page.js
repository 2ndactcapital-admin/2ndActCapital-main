import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import GoldReviewScreen from "@/components/admin/GoldReviewScreen";
import { getGoldCandidates } from "@/lib/api";

// Note gold set (noteextractb1). Super Admin is enforced SERVER-SIDE by
// FastAPI; anyone else who types the URL gets a 403 and the panel below.
export default async function NoteGoldPage() {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/note-extraction/gold");
  }

  let initial = null;
  let error = null;
  try {
    initial = await getGoldCandidates({});
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }

  return (
    <AppShell user={session.user}>
      <div>
        <h1 className="text-3xl font-semibold text-navy">Note Gold Set</h1>
        <p className="mt-1 text-sm text-text-muted">
          Hand-checked note terms. Pick a note, read the trimmed filing beside every reading, and
          confirm or correct each field. Only a reviewer writes a gold value.
        </p>
      </div>
      {error === "forbidden" ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          Super Admin access required.
        </div>
      ) : error ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          Could not load the gold candidates: {error}
        </div>
      ) : (
        <GoldReviewScreen initial={initial} />
      )}
    </AppShell>
  );
}
