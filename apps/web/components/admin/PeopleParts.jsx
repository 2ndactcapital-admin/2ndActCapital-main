"use client";

// USERMGMT1 — the presentational pieces of People & Access. No "@/" imports
// and no server actions: every handler arrives as a prop, so node tests render
// these REAL components from an API envelope (tests/admin/userManagement.test.mjs).
// Every control renders only when the row's server-published `actions` names
// it (`can()` fails closed).

import { useState } from "react";
import {
  ACTION,
  EMAIL_EDIT_NOTE,
  PENDING_EXPLANATION,
  can,
  displayEmail,
  linkCandidates,
  roleLabel,
  signInLines,
  statusLabel,
} from "../../lib/userManagementModel.mjs";

const HAIRLINE = "#ece8dd";

function lastLoginLabel(value) {
  if (!value) return "Never";
  const when = new Date(value);
  if (Number.isNaN(when.getTime())) return "—";
  const days = Math.floor((Date.now() - when.getTime()) / 86400000);
  const date = when.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
  if (days <= 0) return `${date} (today)`;
  if (days === 1) return `${date} (yesterday)`;
  return `${date} (${days}d ago)`;
}

export function RoleChips({ user }) {
  const roles = Array.isArray(user.roles) ? user.roles : [];
  if (roles.length === 0) return <span className="text-text-muted">—</span>;
  return (
    <span className="flex flex-wrap gap-1" data-roles={roles.length}>
      {roles.map((r) => (
        <span
          key={r.id}
          className="inline-flex items-center rounded-full bg-gold-light px-2.5 py-0.5 text-xs font-medium text-navy"
        >
          {roleLabel(r.name)}
        </span>
      ))}
    </span>
  );
}

export function SignInCell({ user }) {
  return (
    <span className="block text-xs text-text-secondary" data-sign-in>
      {signInLines(user).map((line) => (
        <span key={line} className="block">{line}</span>
      ))}
    </span>
  );
}

export function UserRow({ user, onEdit, onRevoke }) {
  const muted = user.is_deleted || user.is_active === false || user.status === "retired";
  return (
    <tr className="border-b border-border last:border-b-0" data-user-row={user.id}>
      <td className="px-4 py-3 font-medium text-text-primary">
        {user.full_name || "—"}
        {user.is_test ? (
          <span className="ml-2 rounded border border-border px-1.5 py-0.5 text-[11px] text-text-muted">Test</span>
        ) : null}
      </td>
      <td className="px-4 py-3 text-text-secondary">
        <span className={user.email_is_placeholder ? "italic text-text-muted" : ""}>{displayEmail(user)}</span>
        {user.contact_email ? (
          <span className="block text-xs text-text-muted">Gave {user.contact_email} (unverified)</span>
        ) : null}
      </td>
      <td className="px-4 py-3"><RoleChips user={user} /></td>
      <td className="px-4 py-3"><SignInCell user={user} /></td>
      <td className="px-4 py-3 text-text-secondary">{lastLoginLabel(user.last_login_at)}</td>
      <td className="px-4 py-3" style={{ color: muted ? "#9B2335" : "var(--2a-text)" }}>
        {statusLabel(user)}
      </td>
      <td className="px-4 py-3 text-right">
        {can(user, ACTION.edit) && user.never_signed_in && user.invite_status === "pending" && onRevoke ? (
          <button
            type="button"
            data-action="revoke-invite"
            onClick={() => onRevoke(user)}
            className="mr-4 text-sm font-medium text-[#9B2335] hover:underline"
          >
            Revoke invite
          </button>
        ) : null}
        {can(user, ACTION.edit) ? (
          <button
            type="button"
            data-action="edit"
            onClick={() => onEdit(user)}
            className="text-sm font-medium text-navy hover:underline"
          >
            Manage
          </button>
        ) : null}
      </td>
    </tr>
  );
}

export function PendingPanel({ pending, users, roles, onApprove, onReject, onLink, busy, error }) {
  const [roleChoice, setRoleChoice] = useState({});
  const [linkChoice, setLinkChoice] = useState({});
  if (!pending || pending.length === 0) return null;
  return (
    <div className="mt-6 rounded-lg border bg-bg-card p-5" style={{ borderColor: HAIRLINE }} data-pending-panel>
      <h2 className="text-lg font-semibold text-navy">Awaiting approval</h2>
      <p className="mt-1 text-sm text-text-muted">{PENDING_EXPLANATION}</p>
      {error ? <p className="mt-2 text-sm text-[#9B2335]">{error}</p> : null}
      <ul className="mt-3 divide-y divide-border">
        {pending.map((u) => {
          const candidates = linkCandidates(users, u);
          const target = linkChoice[u.id] ?? u.likely_match?.id ?? "";
          return (
            <li key={u.id} className="py-4" data-pending-user={u.id}>
              <p className="text-sm text-text-primary">
                {u.full_name || "—"} <span className="text-text-muted">{displayEmail(u)}</span>
              </p>
              <p className="mt-0.5 text-xs text-text-muted">
                {signInLines(u).join(" · ")}
                {u.contact_email ? ` · Gave ${u.contact_email} (unverified)` : ""}
              </p>
              {u.likely_match ? (
                <p className="mt-1 text-sm text-navy" data-likely-match={u.likely_match.id}>
                  Looks like {u.likely_match.full_name || u.likely_match.email}
                  {u.likely_match.full_name ? ` (${u.likely_match.email})` : ""}
                </p>
              ) : null}
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {can(u, ACTION.approve) ? (
                  <>
                    <select
                      value={roleChoice[u.id] || ""}
                      onChange={(e) => setRoleChoice((c) => ({ ...c, [u.id]: e.target.value }))}
                      className="rounded-md border border-border bg-bg-card px-3 py-1.5 text-sm"
                    >
                      <option value="">Choose a role…</option>
                      {roles.map((r) => (
                        <option key={r.id} value={r.id}>{roleLabel(r.name)}</option>
                      ))}
                    </select>
                    <button
                      type="button"
                      data-action="approve"
                      disabled={busy || !roleChoice[u.id]}
                      onClick={() => onApprove(u, roleChoice[u.id])}
                      className="rounded-md bg-navy px-3 py-1.5 text-sm font-medium text-bg-app disabled:opacity-50"
                    >
                      Approve
                    </button>
                  </>
                ) : null}
                {can(u, ACTION.link) ? (
                  <>
                    <select
                      value={target}
                      onChange={(e) => setLinkChoice((c) => ({ ...c, [u.id]: e.target.value }))}
                      className="rounded-md border border-border bg-bg-card px-3 py-1.5 text-sm"
                    >
                      <option value="">Link to an existing person…</option>
                      {candidates.map((c) => (
                        <option key={c.id} value={c.id}>{c.full_name || c.email} — {c.email}</option>
                      ))}
                    </select>
                    <button
                      type="button"
                      data-action="link"
                      disabled={busy || !target}
                      onClick={() => onLink(u, target)}
                      className="rounded-md border border-navy px-3 py-1.5 text-sm font-medium text-navy disabled:opacity-50"
                    >
                      Link sign-in
                    </button>
                  </>
                ) : null}
                {can(u, ACTION.reject) ? (
                  <button
                    type="button"
                    data-action="reject"
                    disabled={busy}
                    onClick={() => onReject(u)}
                    className="rounded-md border border-border px-3 py-1.5 text-sm text-text-primary disabled:opacity-50"
                  >
                    Decline
                  </button>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** The sign-in section of the Manage dialog: reset, re-invite, email. */
export function SignInActions({ user, busy, result, error, onReset, onResend, onSaveEmail }) {
  const [email, setEmail] = useState(user.email_is_placeholder ? "" : user.email || "");
  const showReset = can(user, ACTION.resetPassword);
  const showResend = can(user, ACTION.resendInvite);
  const showEmail = can(user, ACTION.editEmail);
  if (!showReset && !showResend && !showEmail) return null;
  return (
    <div className="mt-6 border-t border-border pt-4" data-sign-in-actions>
      <p className="text-xs font-semibold uppercase" style={{ letterSpacing: "0.22em", color: "var(--2a-gold)" }}>
        Sign-in
      </p>
      <div className="mt-2"><SignInCell user={user} /></div>

      {showReset ? (
        user.reset_password_refusal ? (
          <p className="mt-3 text-sm text-text-secondary" data-reset-refusal>{user.reset_password_refusal}</p>
        ) : (
          <button
            type="button"
            data-action="reset-password"
            disabled={busy}
            onClick={onReset}
            className="mt-3 rounded-md border border-navy px-3 py-1.5 text-sm font-medium text-navy disabled:opacity-60"
          >
            Send password reset email
          </button>
        )
      ) : null}

      {showResend ? (
        <div className="mt-3">
          <button
            type="button"
            data-action="resend-invite"
            disabled={busy}
            onClick={onResend}
            className="rounded-md border border-navy px-3 py-1.5 text-sm font-medium text-navy disabled:opacity-60"
          >
            Resend invitation
          </button>
          <p className="mt-1 text-xs text-text-muted">Issues a new link; the previous link stops working.</p>
        </div>
      ) : null}

      {result?.enrollment_url ? (
        <div className="mt-3" data-enrollment-link>
          <p className="text-sm text-text-secondary">
            {result.email_delivery?.sent
              ? "The invitation was emailed. The link is also here:"
              : "Email delivery is not available, so share this link with the person directly:"}
          </p>
          <p className="mt-1 break-all rounded-md border border-border bg-bg-app p-3 font-mono text-xs text-text-primary">
            {result.enrollment_url}
          </p>
        </div>
      ) : result?.message ? (
        <p className="mt-3 text-sm text-[#2D6A4F]">{result.message}</p>
      ) : null}

      {showEmail ? (
        <div className="mt-4">
          <label className="block text-xs font-medium uppercase tracking-wide text-text-muted">Email address</label>
          <div className="mt-1 flex gap-2">
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="name@example.com"
              className="w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
            />
            <button
              type="button"
              data-action="save-email"
              disabled={busy || !email.trim()}
              onClick={() => onSaveEmail(email.trim())}
              className="rounded-md bg-navy px-3 py-2 text-sm font-medium text-bg-app disabled:opacity-60"
            >
              Save
            </button>
          </div>
          <p className="mt-1 text-xs text-text-muted">{EMAIL_EDIT_NOTE}</p>
        </div>
      ) : null}

      {error ? <p className="mt-3 text-sm text-[#9B2335]">{error}</p> : null}
    </div>
  );
}
