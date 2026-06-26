import { redirect } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import ProfileClient from "@/components/investment-profile/ProfileClient";
import { fetchAPI } from "@/lib/api";
import { isStaff } from "@/lib/roles";

export default async function InvestmentProfilePage({ searchParams }) {
  const session = await auth0.getSession();
  if (!session) {
    redirect("/auth/login?returnTo=/investment-profile");
  }

  const sp = (await searchParams) || {};
  const requestedEntity = typeof sp.entity === "string" ? sp.entity : null;
  const initialTab = typeof sp.tab === "string" ? sp.tab : "profile";
  const staff = isStaff(session.user);

  let entities = [];
  let questions = [];
  let foundationQuestions = [];
  let loadError = null;
  try {
    [entities, questions, foundationQuestions] = await Promise.all([
      fetchAPI("/api/v1/entities"),
      fetchAPI("/api/v1/investment-profile/questions"),
      fetchAPI("/api/v1/investment-profile/questions", {
        searchParams: { category: "foundation" },
      }),
    ]);
  } catch (error) {
    loadError = error.message;
  }

  // Default to the requested entity, else first individual, else first entity.
  const defaultEntity =
    entities.find((e) => e.id === requestedEntity) ||
    entities.find((e) => e.entity_type === "individual") ||
    entities[0];
  const defaultEntityId = defaultEntity?.id || "";

  let initialAnswers = [];
  let initialConversation = null;
  let initialExtractions = [];
  let initialBrief = null;
  if (defaultEntityId && !loadError) {
    const [ans, convo, extr, brief] = await Promise.allSettled([
      fetchAPI(`/api/v1/investment-profile/${defaultEntityId}/answers`),
      fetchAPI(`/api/v1/investment-profile/${defaultEntityId}/conversation`),
      fetchAPI(`/api/v1/investment-profile/${defaultEntityId}/extractions`),
      fetchAPI(`/api/v1/investment-profile/${defaultEntityId}/brief`),
    ]);
    initialAnswers = ans.status === "fulfilled" ? ans.value || [] : [];
    initialConversation = convo.status === "fulfilled" ? convo.value : null;
    initialExtractions = extr.status === "fulfilled" ? extr.value || [] : [];
    initialBrief = brief.status === "fulfilled" ? brief.value : null;
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
            foundationQuestions={foundationQuestions}
            defaultEntityId={defaultEntityId}
            initialAnswers={initialAnswers}
            initialMode={defaultEntity?.profile_mode || "foundation"}
            initialConversation={initialConversation}
            initialExtractions={initialExtractions}
            initialBrief={initialBrief}
            initialTab={initialTab}
            isStaff={staff}
          />
        )}
      </div>
    </AppShell>
  );
}
