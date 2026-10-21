"use client";

// AUTHZ1 — what an account that is signed in but not admitted sees instead of
// the page. No "@/" imports, so the node tests can render it from a fed
// /users/me payload. The server is the gate: every API but /users/me (and,
// USERMGMT1, /users/me/contact-email) already refuses this account; this only
// says so plainly.
//
// USERMGMT1 — when the account has no real email (a placeholder from a login
// whose address Auth0 did not share or that was already taken), the page asks
// for one. It is stored as an UNVERIFIED contact_email for the admins to see;
// it is never used to link or admit the account.

import { useState } from "react";

const COPY = {
  pending: {
    title: "Access pending",
    body: "Access pending — an administrator has been notified. You will be able to continue once your access has been approved.",
    detail:
      "Your sign-in worked, but it did not match a member account here, so an administrator needs to approve it or connect it to your existing account. You do not need to sign in again — reload this page once you hear back.",
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

export const CONTACT_PROMPT =
  "We do not have an email address for this sign-in. Leave one so an administrator can reach you.";
export const CONTACT_NOTE =
  "An administrator will confirm it before it is used. It does not change how you sign in.";

/** True when the page should ask for an email: pending, and none real on file. */
export function needsContactEmail(me) {
  if (!me || me.status !== "pending") return false;
  const placeholder =
    me.email_is_placeholder === true || !me.email || String(me.email).endsWith("@placeholder.local");
  return placeholder && !me.contact_email;
}

async function defaultSubmit(email) {
  const res = await fetch("/api/users/me/contact-email", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ contact_email: email }),
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    // non-JSON
  }
  if (!res.ok) throw new Error(data?.detail || "Could not save that address.");
  return data;
}

function ContactEmailForm({ onSubmit }) {
  const [email, setEmail] = useState("");
  const [saved, setSaved] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  if (saved) {
    return (
      <p data-contact-saved style={{ color: "#2D6A4F", marginTop: 24 }}>
        Thank you. An administrator will see {saved} with your request.
      </p>
    );
  }
  async function submit(e) {
    e.preventDefault();
    const value = email.trim();
    if (!value || !value.includes("@")) {
      setError("Enter a valid email address.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onSubmit(value);
      setSaved(value);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form data-contact-form onSubmit={submit} style={{ marginTop: 24 }}>
      <p style={{ color: "#334155", margin: 0 }}>{CONTACT_PROMPT}</p>
      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="name@example.com"
          style={{ flex: 1, border: "1px solid #E2E8F0", borderRadius: 6, padding: "8px 12px", fontSize: 15 }}
        />
        <button
          type="submit"
          disabled={busy}
          style={{ background: "#1B2B4B", color: "#FAF9F6", border: 0, borderRadius: 6, padding: "8px 16px", fontSize: 15 }}
        >
          Send
        </button>
      </div>
      <p style={{ color: "#64748B", fontSize: 14, marginTop: 8 }}>{CONTACT_NOTE}</p>
      {error ? <p style={{ color: "#9B2335", fontSize: 14 }}>{error}</p> : null}
    </form>
  );
}

export default function AccessPending({ me, onSubmitContactEmail = defaultSubmit }) {
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
      {copy.detail ? (
        <p style={{ color: "#64748B", marginTop: 12, lineHeight: 1.6, fontSize: 15 }}>{copy.detail}</p>
      ) : null}
      {needsContactEmail(me) ? <ContactEmailForm onSubmit={onSubmitContactEmail} /> : null}
      {me?.status === "pending" && me?.contact_email ? (
        <p style={{ color: "#64748B", marginTop: 16, fontSize: 15 }}>
          Contact address left for the administrators: {me.contact_email}
        </p>
      ) : null}
      {me?.email && !String(me.email).endsWith("@placeholder.local") ? (
        <p style={{ color: "#64748B", marginTop: 24, fontSize: 15 }}>Signed in as {me.email}</p>
      ) : null}
    </section>
  );
}
