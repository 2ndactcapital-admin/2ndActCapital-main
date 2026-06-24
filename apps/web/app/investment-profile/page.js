import { redirect } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import ProfileClient from "@/components/investment-profile/ProfileClient";
import { fetchAPI } from "@/lib/api";

export default async function InvestmentProfilePage() {
  const session = await auth0.getSession();
  if (!session) {
    redirect("/auth/login?returnTo=/investment-profile");
  }

  let entities = [];
  let questions = [];
  let loadError = null;
  try {
    [entities, questions] = await Promise.all([
      fetchAPI("/api/v1/entities"),
      fetchAPI("/api/v1/investment-profile/questions"),
    ]);
  } catch (error) {
    loadError = error.message;
  }

  // Default to the first individual entity, else the first entity.
  const defaultEntity =
    entities.find((e) => e.entity_type === "individual") || entities[0];
  const defaultEntityId = defaultEntity?.id || "";

  let initialAnswers = [];
  if (defaultEntityId && !loadError) {
    try {
      initialAnswers = await fetchAPI(
        `/api/v1/investment-profile/${defaultEntityId}/answers`,
      );
    } catch {
      initialAnswers = [];
    }
  }

  return (
    <AppShell user={session.user}>
      <h1 className="text-2xl font-semibold text-navy">Investment Profile</h1>
      <p className="mt-1 text-sm text-text-muted">
        Capture the investor profile used to qualify deals and SPVs.
      </p>

      <div className="mt-8">
        {loadError ? (
          <div className="rounded-lg border border-border bg-bg-card p-8 text-center text-sm text-text-muted">
            Could not load the investment profile: {loadError}
          </div>
        ) : (
          <ProfileClient
            entities={entities}
            questions={questions}
            defaultEntityId={defaultEntityId}
            initialAnswers={initialAnswers}
          />
        )}
      </div>
    </AppShell>
  );
}
