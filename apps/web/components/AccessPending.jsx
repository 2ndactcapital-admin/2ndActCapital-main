// AUTHZ1 — what an account that is signed in but not admitted sees instead of
// the page. Pure (no hooks, no "@/" imports) so the node tests can render it
// from a fed /users/me payload. The server is the gate: every API but
// /users/me already refuses this account; this only says so plainly.

const COPY = {
  pending: {
    title: "Access pending",
    body: "Access pending — an administrator has been notified. You will be able to continue once your access has been approved.",
  },
  rejected: {
    title: "Access not granted",
    body: "An administrator has declined access for this sign-in. Contact your administrator if you believe this is a mistake.",
  },
  retired: {
    title: "This sign-in is no longer in use",
    body: "This sign-in is no longer linked to an active account. Sign in with your usual account, or contact your administrator.",
  },
};

/** True when a /users/me payload says the account is not admitted. */
export function isAccessPending(me) {
  return Boolean(me && typeof me === "object" && me.status && me.status !== "active");
}

export default function AccessPending({ me }) {
  const copy = COPY[me?.status] ?? COPY.pending;
  return (
    <section
      data-access-status={me?.status ?? "pending"}
      style={{
        maxWidth: 560,
        margin: "64px auto",
        background: "#FFFFFF",
        border: "1px solid #ece8dd",
        borderRadius: 6,
        padding: "40px 44px",
      }}
    >
      <h1 style={{ fontFamily: "'Spectral', Georgia, serif", fontWeight: 400, fontSize: 30, color: "#1B2B4B", margin: 0 }}>
        {copy.title}
      </h1>
      <p style={{ color: "#334155", marginTop: 16, lineHeight: 1.6 }}>{copy.body}</p>
      {me?.email && !String(me.email).endsWith("@placeholder.local") ? (
        <p style={{ color: "#64748B", marginTop: 24, fontSize: 15 }}>Signed in as {me.email}</p>
      ) : null}
    </section>
  );
}
