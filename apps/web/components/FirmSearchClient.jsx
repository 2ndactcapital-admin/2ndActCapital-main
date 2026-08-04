"use client";

/**
 * Firm-search interstitial (Hollisworks Login/Enroll). Same brand system as the
 * marketing page — its own holly/bronze/paper tokens, no 2nd Act palette.
 *
 * The `intent` prop ("login" | "enroll") is remembered from whichever button
 * the prospect clicked on the marketing page. On submit we query the public
 * matcher; on a confident match we hard-redirect to the firm's real stored
 * login/enroll URL (cross-origin to the tenant/admin host, hence
 * window.location). Ambiguous / no-match render a clarify-and-retry message —
 * never a pick-list, never a guessed closest firm.
 */

import { useState } from "react";

const CSS = `
.fs-root{
  --paper:#FBFAF8; --surface:#FFFFFF;
  --ink:#16211C; --ink-2:#4A554F; --ink-3:#7C877F;
  --holly:#1F4034; --holly-deep:#16302A; --holly-tint:#EEF3F0;
  --bronze:#8A6220; --bronze-tint:#FAF4E8;
  --rule:#E3E0D9; --rule-soft:#EDEBE5;
  --display:"Libre Caslon Display", Georgia, serif;
  --text:"Libre Caslon Text", Georgia, serif;
  --sans:"IBM Plex Sans", -apple-system, BlinkMacSystemFont, sans-serif;
  --mono:"IBM Plex Mono", ui-monospace, SFMono-Regular, monospace;
  min-height:100vh; background:var(--paper); color:var(--ink);
  font-family:var(--sans); font-size:17px; line-height:1.6;
  display:flex; flex-direction:column;
  -webkit-font-smoothing:antialiased;
}
.fs-root *,.fs-root *::before,.fs-root *::after{ box-sizing:border-box; }
.fs-root a{ color:inherit; }
.fs-root :focus-visible{ outline:2px solid var(--holly); outline-offset:3px; border-radius:2px; }
.fs-mast{
  border-bottom:1px solid var(--rule-soft);
  padding:1.25rem clamp(1.25rem,5vw,4rem);
}
.fs-mark{
  font-family:var(--display); font-size:1.45rem; letter-spacing:-.01em;
  color:var(--holly); text-decoration:none;
}
.fs-mark .dot{ color:var(--bronze); }
.fs-main{
  flex:1; display:flex; align-items:center; justify-content:center;
  padding:clamp(2rem,6vw,4rem) clamp(1.25rem,5vw,4rem);
}
.fs-card{
  width:100%; max-width:520px;
  background:var(--surface); border:1px solid var(--rule);
  border-radius:4px; padding:clamp(1.75rem,4vw,2.75rem);
  box-shadow:0 12px 32px -22px rgba(22,33,28,.22);
}
.fs-eyebrow{
  font-family:var(--mono); font-size:.72rem; letter-spacing:.14em;
  text-transform:uppercase; color:var(--ink-3); margin:0 0 1rem;
}
.fs-card h1{
  font-family:var(--display); font-weight:400;
  font-size:clamp(1.7rem,3.4vw,2.3rem); line-height:1.1;
  letter-spacing:-.015em; margin:0 0 .75rem;
}
.fs-lede{
  font-family:var(--text); font-size:1.05rem; color:var(--ink-2);
  margin:0 0 1.75rem;
}
.fs-field label{
  display:block; font-family:var(--mono); font-size:.7rem;
  letter-spacing:.12em; text-transform:uppercase; color:var(--ink-3);
  margin-bottom:.45rem;
}
.fs-field input{
  width:100%; font-family:var(--sans); font-size:.98rem; color:var(--ink);
  background:var(--paper); border:1px solid var(--rule);
  border-radius:3px; padding:.75rem .85rem;
  transition:border-color .18s ease, background .18s ease;
}
.fs-field input:focus{ background:var(--surface); border-color:var(--holly); outline:none; }
.fs-foot{ display:flex; align-items:center; gap:1rem; flex-wrap:wrap; margin-top:1.4rem; }
.fs-btn{
  font-family:var(--sans); font-size:1rem; font-weight:500;
  padding:.85rem 1.6rem; border-radius:3px; border:1px solid transparent;
  background:var(--holly); color:#F7F9F7; cursor:pointer;
  transition:background .18s ease;
}
.fs-btn:hover{ background:var(--holly-deep); }
.fs-btn:disabled{ opacity:.6; cursor:default; }
.fs-back{
  font-family:var(--sans); font-size:.9rem; color:var(--ink-2);
  text-decoration:none; border-bottom:1px solid transparent;
}
.fs-back:hover{ color:var(--holly); border-bottom-color:var(--bronze); }
.fs-msg{
  margin-top:1.35rem; padding:.9rem 1rem; border-radius:3px;
  font-size:.92rem; line-height:1.5;
}
.fs-msg.err{ background:var(--bronze-tint); color:#6b4c19; border:1px solid #E7D8B8; }
.fs-msg.ok{ background:var(--holly-tint); color:var(--holly-deep); border:1px solid #D7E2DA; }
`;

export default function FirmSearchClient({ intent }) {
  const [value, setValue] = useState("");
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState(null); // { kind: "err"|"ok", text }

  const isEnroll = intent === "enroll";
  const heading = isEnroll ? "Enroll your firm" : "Log in to your firm";
  const verb = isEnroll ? "enroll" : "log in";

  async function handleSubmit(e) {
    e.preventDefault();
    const q = value.trim();
    if (!q) {
      setMsg({ kind: "err", text: "Enter your firm's name to continue." });
      return;
    }
    setLoading(true);
    setMsg(null);
    try {
      const url = new URL("/api/marketing/firm-search", window.location.origin);
      url.searchParams.set("q", q);
      url.searchParams.set("intent", intent);
      const res = await fetch(url, { cache: "no-store" });
      const data = await res.json().catch(() => ({}));

      if (data.status === "matched" && data.redirect_url) {
        setMsg({
          kind: "ok",
          text: `Taking you to ${data.org_name || "your firm"}…`,
        });
        // Cross-origin to the firm's own subdomain (or the admin host) — a full
        // navigation, not a client route push.
        window.location.assign(data.redirect_url);
        return;
      }

      // Ambiguous or no confident match: clarify and let them retry. We never
      // present a pick-list or auto-pick the closest firm.
      setMsg({
        kind: "err",
        text:
          data.message ||
          "We couldn't confidently match that firm. Please try the full, exact name.",
      });
    } catch {
      setMsg({
        kind: "err",
        text: "Something went wrong. Please try again, or email hello@hollisworks.com.",
      });
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="fs-root">
      <link rel="preconnect" href="https://fonts.googleapis.com" />
      <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
      <link
        href="https://fonts.googleapis.com/css2?family=Libre+Caslon+Display&family=Libre+Caslon+Text:ital,wght@0,400;0,700;1,400&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;450;500;600&display=swap"
        rel="stylesheet"
      />
      <style dangerouslySetInnerHTML={{ __html: CSS }} />

      <header className="fs-mast">
        <a className="fs-mark" href="/">
          Hollisworks<span className="dot">.</span>
        </a>
      </header>

      <main className="fs-main">
        <div className="fs-card">
          <p className="fs-eyebrow">{isEnroll ? "Enrollment" : "Sign in"}</p>
          <h1>{heading}</h1>
          <p className="fs-lede">
            Enter your firm&rsquo;s name and we&rsquo;ll take you to the right
            place to {verb}.
          </p>

          <form onSubmit={handleSubmit} noValidate>
            <div className="fs-field">
              <label htmlFor="fs-firm">Firm name</label>
              <input
                id="fs-firm"
                name="firm"
                type="text"
                autoComplete="organization"
                autoFocus
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder="e.g. Northstar Capital"
              />
            </div>
            <div className="fs-foot">
              <button className="fs-btn" type="submit" disabled={loading}>
                {loading ? "Searching…" : "Continue"}
              </button>
              <a className="fs-back" href="/">
                Back
              </a>
            </div>
          </form>

          {msg && (
            <p className={`fs-msg ${msg.kind}`} role="status">
              {msg.text}
            </p>
          )}
        </div>
      </main>
    </div>
  );
}
