"use client";

/**
 * Hollisworks marketing page — the platform's own apex site (bare
 * hollisworks.com / www.hollisworks.com). Faithful conversion of the provided
 * HTML/CSS/JS: colours, fonts, copy, layout and the animated activity log are
 * preserved exactly. This page carries its OWN brand tokens (--holly / --bronze
 * / --paper …) scoped to `.hw-root` — it deliberately shares nothing with 2nd
 * Act's Signature palette (--2a-*).
 *
 * Wiring added on top of the static design:
 *   - "Log in" / "Enroll" -> the shared firm-search interstitial, each carrying
 *     its intent (?intent=login | ?intent=enroll).
 *   - The contact form POSTs to the Next API route /api/marketing/contact
 *     (which forwards to FastAPI POST /api/v1/marketing/contact) instead of
 *     faking success.
 */

import { useEffect, useRef, useState } from "react";

const CSS = `
.hw-root{
  --paper:        #FBFAF8;
  --paper-alt:    #F4F2ED;
  --surface:      #FFFFFF;

  --ink:          #16211C;
  --ink-2:        #4A554F;
  --ink-3:        #7C877F;

  --holly:        #1F4034;
  --holly-deep:   #16302A;
  --holly-tint:   #EEF3F0;

  --bronze:       #8A6220;
  --bronze-tint:  #FAF4E8;

  --rule:         #E3E0D9;
  --rule-soft:    #EDEBE5;

  --display: "Libre Caslon Display", Georgia, serif;
  --text:    "Libre Caslon Text", Georgia, serif;
  --sans:    "IBM Plex Sans", -apple-system, BlinkMacSystemFont, sans-serif;
  --mono:    "IBM Plex Mono", ui-monospace, SFMono-Regular, monospace;

  --gutter: clamp(1.25rem, 5vw, 4rem);
  --maxw: 1180px;

  background:var(--paper);
  color:var(--ink);
  font-family:var(--sans);
  font-size:17px;
  line-height:1.6;
  -webkit-font-smoothing:antialiased;
  text-rendering:optimizeLegibility;
}

.hw-root *,.hw-root *::before,.hw-root *::after{ box-sizing:border-box; }

.hw-root a{ color:inherit; }

.hw-root :focus-visible{
  outline:2px solid var(--holly);
  outline-offset:3px;
  border-radius:2px;
}

.hw-root .skip{
  position:absolute; left:-9999px; top:0;
  background:var(--holly); color:#fff;
  padding:.75rem 1.25rem; z-index:100;
  font-size:.9rem; text-decoration:none;
}
.hw-root .skip:focus{ left:.5rem; top:.5rem; }

.hw-root .wrap{ max-width:var(--maxw); margin:0 auto; padding-inline:var(--gutter); }

/* ---------------- header ---------------- */
.hw-root .masthead{
  position:sticky; top:0; z-index:50;
  background:rgba(251,250,248,.88);
  backdrop-filter:saturate(180%) blur(12px);
  border-bottom:1px solid var(--rule-soft);
}
.hw-root .masthead-in{
  display:flex; align-items:center; gap:1.5rem;
  min-height:74px;
}
.hw-root .mark{
  font-family:var(--display);
  font-size:1.45rem;
  letter-spacing:-.01em;
  color:var(--holly);
  text-decoration:none;
  white-space:nowrap;
}
.hw-root .mark .dot{ color:var(--bronze); }

.hw-root .mainnav{
  display:flex; gap:1.75rem;
  margin-left:auto;
  font-size:.9rem;
}
.hw-root .mainnav a{
  text-decoration:none;
  color:var(--ink-2);
  padding-block:.35rem;
  border-bottom:1px solid transparent;
  transition:color .18s ease, border-color .18s ease;
}
.hw-root .mainnav a:hover{ color:var(--holly); border-bottom-color:var(--bronze); }

.hw-root .authset{ display:flex; align-items:center; gap:.6rem; }

.hw-root .btn{
  font-family:var(--sans);
  font-size:.9rem; font-weight:500;
  padding:.6rem 1.15rem;
  border-radius:3px;
  text-decoration:none;
  border:1px solid transparent;
  cursor:pointer;
  transition:background .18s ease, border-color .18s ease, color .18s ease;
  white-space:nowrap;
  display:inline-block;
}
.hw-root .btn-quiet{ color:var(--ink-2); border-color:var(--rule); background:transparent; }
.hw-root .btn-quiet:hover{ color:var(--holly); border-color:var(--holly); }
.hw-root .btn-solid{ background:var(--holly); color:#F7F9F7; }
.hw-root .btn-solid:hover{ background:var(--holly-deep); }
.hw-root .btn-lg{ font-size:1rem; padding:.85rem 1.6rem; }

.hw-root .navtoggle{
  display:none; margin-left:auto;
  background:none; border:1px solid var(--rule);
  border-radius:3px; padding:.5rem .7rem;
  font-family:var(--sans); font-size:.85rem; color:var(--ink-2);
  cursor:pointer;
}

/* ---------------- hero ---------------- */
.hw-root .hero{ padding-block:clamp(3.5rem,8vw,6rem) clamp(2.5rem,5vw,4rem); }
.hw-root .eyebrow{
  font-family:var(--mono);
  font-size:.72rem;
  letter-spacing:.14em;
  text-transform:uppercase;
  color:var(--ink-3);
  margin:0 0 1.5rem;
}
.hw-root .hero h1{
  font-family:var(--display);
  font-weight:400;
  font-size:clamp(2.3rem, 4.6vw, 3.5rem);
  line-height:1.08;
  letter-spacing:-.015em;
  margin:0 0 1.4rem;
  max-width:20ch;
}
.hw-root .lede{
  font-family:var(--text);
  font-size:clamp(1.1rem,2vw,1.32rem);
  line-height:1.55;
  color:var(--ink-2);
  max-width:46ch;
  margin:0 0 2.25rem;
}
.hw-root .hero-cta{ display:flex; flex-wrap:wrap; gap:.85rem; align-items:center; margin-top:clamp(1.75rem,3.5vw,2.5rem); }
.hw-root .hero-note{
  font-family:var(--mono); font-size:.78rem;
  color:var(--ink-3); margin-left:.35rem;
}

/* ---------------- the log (signature) ---------------- */
.hw-root .logcard{
  background:var(--surface);
  border:1px solid var(--rule);
  border-radius:4px;
  box-shadow:0 1px 2px rgba(22,33,28,.03), 0 12px 32px -18px rgba(22,33,28,.18);
  overflow:hidden;
}
.hw-root .logcard-head{
  display:flex; align-items:baseline; gap:1rem; flex-wrap:wrap;
  padding:1.15rem clamp(1.1rem,3vw,2rem);
  border-bottom:1px solid var(--rule-soft);
  background:linear-gradient(to bottom, #FDFCFA, var(--surface));
}
.hw-root .logcard-head h2{
  font-family:var(--display); font-weight:400;
  font-size:1.15rem; margin:0; letter-spacing:.01em;
}
.hw-root .logcard-head .when{
  font-family:var(--mono); font-size:.75rem;
  color:var(--ink-3); margin-left:auto;
}
.hw-root .loglist{ list-style:none; margin:0; padding:.4rem 0; }
.hw-root .loglist li{
  display:grid;
  grid-template-columns:auto 1fr auto;
  gap:.35rem 1.25rem;
  align-items:baseline;
  padding:.85rem clamp(1.1rem,3vw,2rem);
  border-bottom:1px solid var(--rule-soft);
}
.hw-root .loglist li:last-child{ border-bottom:0; }
.hw-root .stamp{
  font-family:var(--mono); font-size:.76rem;
  color:var(--ink-3); font-variant-numeric:tabular-nums;
}
.hw-root .entry{ font-size:.98rem; color:var(--ink); line-height:1.5; }
.hw-root .entry b{ font-weight:600; }
.hw-root .entry .sub{ display:block; color:var(--ink-3); font-size:.88rem; margin-top:.15rem; }

.hw-root .state{
  font-family:var(--mono);
  font-size:.68rem; letter-spacing:.1em; text-transform:uppercase;
  padding:.22rem .55rem; border-radius:2px; white-space:nowrap;
}
.hw-root .state-done{ background:var(--holly-tint); color:var(--holly); }
.hw-root .state-wait{ background:var(--bronze-tint); color:var(--bronze); }

.hw-root .logcard-foot{
  padding:1rem clamp(1.1rem,3vw,2rem);
  background:var(--paper-alt);
  border-top:1px solid var(--rule-soft);
  font-family:var(--text); font-style:italic;
  font-size:.95rem; color:var(--ink-2);
}

/* staggered arrival */
.hw-root .loglist li{ opacity:0; transform:translateY(6px); }
.hw-root .loglist.in li{ animation:hw-arrive .5s ease forwards; }
.hw-root .loglist.in li:nth-child(1){ animation-delay:.05s }
.hw-root .loglist.in li:nth-child(2){ animation-delay:.28s }
.hw-root .loglist.in li:nth-child(3){ animation-delay:.51s }
.hw-root .loglist.in li:nth-child(4){ animation-delay:.74s }
@keyframes hw-arrive{ to{ opacity:1; transform:none; } }
@media (prefers-reduced-motion: reduce){
  .hw-root .loglist li, .hw-root .loglist.in li{ opacity:1; transform:none; animation:none; }
}

/* ---------------- verbs ---------------- */
.hw-root .band{ padding-block:clamp(3.5rem,7vw,5.5rem); }
.hw-root .band-alt{ background:var(--paper-alt); border-block:1px solid var(--rule-soft); }

.hw-root .band-intro{ max-width:52ch; margin-bottom:clamp(2.5rem,5vw,3.5rem); }
.hw-root .band-intro h2{
  font-family:var(--display); font-weight:400;
  font-size:clamp(1.9rem,4vw,2.7rem);
  line-height:1.15; letter-spacing:-.015em; margin:0 0 .85rem;
}
.hw-root .band-intro p{
  font-family:var(--text); font-size:1.1rem;
  color:var(--ink-2); margin:0;
}

.hw-root .verbs{ display:grid; gap:0; border-top:1px solid var(--rule); }
.hw-root .verb{
  display:grid;
  grid-template-columns:minmax(0,7.5rem) minmax(0,1fr) minmax(0,1.05fr);
  gap:1rem clamp(1.5rem,4vw,3.5rem);
  padding-block:clamp(1.75rem,3.5vw,2.5rem);
  border-bottom:1px solid var(--rule);
  align-items:start;
}
.hw-root .verb-name{
  font-family:var(--display); font-weight:400;
  font-size:clamp(1.5rem,3vw,2rem);
  line-height:1.05; letter-spacing:-.01em;
  color:var(--holly);
}
.hw-root .verb-name span{
  display:block;
  font-family:var(--mono); font-size:.68rem;
  letter-spacing:.14em; text-transform:uppercase;
  color:var(--ink-3); margin-bottom:.5rem;
}
.hw-root .verb-body p{
  font-family:var(--text); font-size:1.06rem;
  line-height:1.55; margin:0; color:var(--ink);
}
.hw-root .verb-list{
  list-style:none; margin:0; padding:0;
  font-family:var(--sans); font-size:.9rem; color:var(--ink-2);
}
.hw-root .verb-list li{
  padding:.42rem 0 .42rem 1.1rem;
  border-bottom:1px solid var(--rule-soft);
  position:relative;
}
.hw-root .verb-list li:last-child{ border-bottom:0; }
.hw-root .verb-list li::before{
  content:""; position:absolute; left:0; top:1.05em;
  width:5px; height:1px; background:var(--bronze);
}

/* ---------------- asks (boundary) ---------------- */
.hw-root .asks{
  background:var(--holly-tint);
  border:1px solid #D7E2DA;
  border-left:3px solid var(--holly);
  border-radius:4px;
  padding:clamp(2rem,4.5vw,3.25rem);
  display:grid;
  grid-template-columns:minmax(0,1fr) minmax(0,1fr);
  gap:clamp(1.5rem,4vw,3.5rem);
  align-items:center;
}
.hw-root .asks h2{
  font-family:var(--display); font-weight:400;
  font-size:clamp(1.8rem,3.6vw,2.5rem);
  line-height:1.12; margin:0 0 .9rem; color:var(--holly-deep);
}
.hw-root .asks p{
  font-family:var(--text); font-size:1.08rem;
  line-height:1.6; color:var(--ink-2); margin:0 0 1rem;
}
.hw-root .asks p:last-child{ margin-bottom:0; }
.hw-root .gates{ list-style:none; margin:0; padding:0; }
.hw-root .gates li{
  background:var(--surface);
  border:1px solid #DDE6E0;
  border-radius:3px;
  padding:.85rem 1rem;
  margin-bottom:.6rem;
  font-size:.92rem;
  display:flex; gap:.85rem; align-items:baseline;
}
.hw-root .gates li:last-child{ margin-bottom:0; }
.hw-root .tier{
  font-family:var(--mono); font-size:.66rem;
  letter-spacing:.1em; text-transform:uppercase;
  color:var(--ink-3); white-space:nowrap;
  min-width:3.6rem;
}

/* ---------------- contact ---------------- */
.hw-root .contact{
  display:grid;
  grid-template-columns:minmax(0,.85fr) minmax(0,1fr);
  gap:clamp(2rem,5vw,4.5rem);
  align-items:start;
}
.hw-root .contact-copy h2{
  font-family:var(--display); font-weight:400;
  font-size:clamp(1.9rem,4vw,2.7rem);
  line-height:1.12; margin:0 0 .9rem; letter-spacing:-.015em;
}
.hw-root .contact-copy p{
  font-family:var(--text); font-size:1.08rem;
  color:var(--ink-2); margin:0 0 1.5rem;
}
.hw-root .contact-direct{
  font-family:var(--mono); font-size:.85rem;
  color:var(--ink-2); padding-top:1.25rem;
  border-top:1px solid var(--rule);
}
.hw-root .contact-direct a{ color:var(--holly); }

.hw-root .form{
  background:var(--surface);
  border:1px solid var(--rule);
  border-radius:4px;
  padding:clamp(1.5rem,3.5vw,2.25rem);
  box-shadow:0 12px 32px -22px rgba(22,33,28,.22);
}
.hw-root .field{ margin-bottom:1.1rem; }
.hw-root .field label{
  display:block;
  font-family:var(--mono); font-size:.7rem;
  letter-spacing:.12em; text-transform:uppercase;
  color:var(--ink-3); margin-bottom:.45rem;
}
.hw-root .field input, .hw-root .field select, .hw-root .field textarea{
  width:100%;
  font-family:var(--sans); font-size:.95rem; color:var(--ink);
  background:var(--paper);
  border:1px solid var(--rule);
  border-radius:3px;
  padding:.7rem .8rem;
  transition:border-color .18s ease, background .18s ease;
}
.hw-root .field textarea{ min-height:112px; resize:vertical; }
.hw-root .field input:focus, .hw-root .field select:focus, .hw-root .field textarea:focus{
  background:var(--surface); border-color:var(--holly); outline:none;
}
.hw-root .field-row{ display:grid; grid-template-columns:1fr 1fr; gap:1.1rem; }
.hw-root .form-foot{
  display:flex; align-items:center; gap:1rem; flex-wrap:wrap;
  margin-top:1.35rem; padding-top:1.35rem;
  border-top:1px solid var(--rule-soft);
}
.hw-root .form-note{ font-size:.8rem; color:var(--ink-3); max-width:30ch; }
.hw-root .form-status{
  margin-top:1rem; font-family:var(--mono); font-size:.8rem;
  color:var(--holly); display:none;
}
.hw-root .form-status.on{ display:block; }
.hw-root .form-status.err{ color:var(--bronze); }

/* ---------------- footer ---------------- */
.hw-root .foot{
  background:var(--holly-deep);
  color:#C6D2CA;
  padding-block:clamp(3rem,6vw,4.5rem) 2rem;
  margin-top:clamp(3.5rem,7vw,5.5rem);
}
.hw-root .foot-grid{
  display:grid;
  grid-template-columns:minmax(0,1.4fr) repeat(3, minmax(0,1fr));
  gap:clamp(1.75rem,4vw,3rem);
  padding-bottom:2.5rem;
  border-bottom:1px solid rgba(255,255,255,.12);
}
.hw-root .foot .mark{ color:#F2F6F3; font-size:1.35rem; }
.hw-root .foot .mark .dot{ color:var(--bronze); }
.hw-root .foot-blurb{
  font-family:var(--text); font-size:.95rem;
  color:#9FB0A6; margin:.9rem 0 0; max-width:34ch; line-height:1.55;
}
.hw-root .foot h3{
  font-family:var(--mono); font-size:.68rem;
  letter-spacing:.14em; text-transform:uppercase;
  color:#7E9187; margin:0 0 1rem; font-weight:500;
}
.hw-root .foot ul{ list-style:none; margin:0; padding:0; }
.hw-root .foot li{ margin-bottom:.6rem; }
.hw-root .foot a{
  color:#C6D2CA; text-decoration:none; font-size:.9rem;
  border-bottom:1px solid transparent;
  transition:color .18s ease, border-color .18s ease;
}
.hw-root .foot a:hover{ color:#F2F6F3; border-bottom-color:var(--bronze); }

.hw-root .legal{ padding-top:2rem; }
.hw-root .legal p{
  font-size:.79rem; line-height:1.65;
  color:#84978C;
  margin:0 0 .9rem; max-width:88ch;
}
.hw-root .legal-bar{
  display:flex; flex-wrap:wrap; gap:.5rem 1.5rem;
  align-items:center; margin-top:1.5rem;
  font-size:.79rem; color:#7E9187;
}
.hw-root .legal-bar a{ font-size:.79rem; color:#9FB0A6; }
.hw-root .legal-bar .sep{ color:#4E6157; }

/* ---------------- responsive ---------------- */
@media (max-width: 900px){
  .hw-root .verb{ grid-template-columns:1fr; gap:.85rem; }
  .hw-root .verb-name{ display:flex; align-items:baseline; gap:.85rem; }
  .hw-root .verb-name span{ margin-bottom:0; }
  .hw-root .asks{ grid-template-columns:1fr; }
  .hw-root .contact{ grid-template-columns:1fr; }
  .hw-root .foot-grid{ grid-template-columns:1fr 1fr; }
}
@media (max-width: 720px){
  .hw-root .mainnav{
    display:none; position:absolute; top:74px; left:0; right:0;
    background:var(--surface); border-bottom:1px solid var(--rule);
    flex-direction:column; gap:0; padding:.5rem var(--gutter) 1rem;
  }
  .hw-root .mainnav.open{ display:flex; }
  .hw-root .mainnav a{ padding-block:.7rem; border-bottom:1px solid var(--rule-soft); }
  .hw-root .navtoggle{ display:block; }
  .hw-root .authset{ margin-left:.5rem; }
  .hw-root .btn{ padding:.55rem .85rem; font-size:.85rem; }
  .hw-root .loglist li{ grid-template-columns:1fr auto; }
  .hw-root .loglist .stamp{ grid-column:1 / -1; }
  .hw-root .field-row{ grid-template-columns:1fr; }
  .hw-root .foot-grid{ grid-template-columns:1fr; }
}
`;

export default function HollisworksMarketing() {
  const [navOpen, setNavOpen] = useState(false);
  const [status, setStatus] = useState({ state: "idle", message: "" });
  const [submitting, setSubmitting] = useState(false);
  const logRef = useRef(null);
  const formRef = useRef(null);

  // Staggered arrival of the activity log, exactly as the source JS: reveal on
  // scroll into view (or immediately when reduced-motion / no IO support).
  useEffect(() => {
    const log = logRef.current;
    if (!log) return;
    const reduce =
      typeof window !== "undefined" &&
      window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce || typeof IntersectionObserver === "undefined") {
      log.classList.add("in");
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            log.classList.add("in");
            io.disconnect();
          }
        });
      },
      { threshold: 0.25 },
    );
    io.observe(log);
    return () => io.disconnect();
  }, []);

  const closeNav = () => setNavOpen(false);

  async function handleSubmit(e) {
    e.preventDefault();
    const form = formRef.current;
    if (form && !form.checkValidity()) {
      form.reportValidity();
      return;
    }
    setSubmitting(true);
    setStatus({ state: "idle", message: "" });
    const data = new FormData(form);
    const payload = {
      name: (data.get("name") || "").toString(),
      firm: (data.get("firm") || "").toString(),
      email: (data.get("email") || "").toString(),
      aum: (data.get("aum") || "").toString(),
      note: (data.get("note") || "").toString(),
    };
    try {
      const res = await fetch("/api/marketing/contact", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error("Request failed");
      setStatus({
        state: "ok",
        message: "Thank you — your note is on its way.",
      });
      form.reset();
    } catch {
      setStatus({
        state: "err",
        message:
          "Something went wrong sending your note. Please email hello@hollisworks.com.",
      });
      setSubmitting(false);
    }
  }

  return (
    <div className="hw-root" id="top">
      {/* Fonts + scoped stylesheet. In App Router these hoist into <head>. */}
      <link rel="preconnect" href="https://fonts.googleapis.com" />
      <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
      <link
        href="https://fonts.googleapis.com/css2?family=Libre+Caslon+Display&family=Libre+Caslon+Text:ital,wght@0,400;0,700;1,400&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;450;500;600&display=swap"
        rel="stylesheet"
      />
      <style dangerouslySetInnerHTML={{ __html: CSS }} />

      <a className="skip" href="#main">
        Skip to content
      </a>

      <header className="masthead">
        <div className="wrap masthead-in">
          <a className="mark" href="#top">
            Hollisworks<span className="dot">.</span>
          </a>
          <button
            className="navtoggle"
            id="navtoggle"
            aria-expanded={navOpen ? "true" : "false"}
            aria-controls="mainnav"
            onClick={() => setNavOpen((o) => !o)}
          >
            Menu
          </button>
          <nav
            className={`mainnav${navOpen ? " open" : ""}`}
            id="mainnav"
            aria-label="Main"
            onClick={(e) => {
              if (e.target.tagName === "A") closeNav();
            }}
          >
            <a href="#what">What Hollis does</a>
            <a href="#asks">Where it stops</a>
            <a href="#contact">Contact</a>
          </nav>
          <div className="authset">
            <a className="btn btn-quiet" href="/firm-search?intent=login">
              Log in
            </a>
            <a className="btn btn-solid" href="/firm-search?intent=enroll">
              Enroll
            </a>
          </div>
        </div>
      </header>

      <main id="main">
        {/* ============ HERO ============ */}
        <section className="hero wrap">
          <p className="eyebrow">AI orchestration for the modern RIA</p>
          <h1>Hollis works. For you.</h1>
          <p className="lede">
            Hollis orchestrates your operations and client service — reconciling
            the book, handling the paperwork, and preparing client work before
            you sit down to it. Your team spends its hours on advice rather than
            administration.
          </p>
        </section>

        {/* ============ THE FIVE VERBS ============ */}
        <section className="band band-alt" id="what">
          <div className="wrap">
            <div className="band-intro">
              <h2>What Hollis can do</h2>
              <p>
                Not modules you have to wire together. Work that happens on your
                book, whether or not you asked this morning.
              </p>
            </div>

            <div className="verbs">
              <article className="verb">
                <h3 className="verb-name">
                  <span>01</span>Hollis maps
                </h3>
                <div className="verb-body">
                  <p>
                    Every entity, every ownership percentage, every layer of
                    look-through — and what all of it looked like on any date you
                    name.
                  </p>
                </div>
                <ul className="verb-list">
                  <li>Bi-temporal ownership graph with as-of restatement</li>
                  <li>Look-through to ultimate beneficial exposure</li>
                  <li>Trusts, holdcos, SPVs, and classes in one structure</li>
                  <li>Time travel to any prior position of the map</li>
                </ul>
              </article>

              <article className="verb">
                <h3 className="verb-name">
                  <span>02</span>Hollis watches
                </h3>
                <div className="verb-body">
                  <p>
                    Drift, breach, and stall — caught when they happen rather
                    than at quarterly review. On the book and on the business.
                  </p>
                </div>
                <ul className="verb-list">
                  <li>Allocation drift against policy bands</li>
                  <li>Investment policy breaches, with the date first crossed</li>
                  <li>Pipeline aging and stage stalls</li>
                  <li>Revenue and client profitability against forecast</li>
                </ul>
              </article>

              <article className="verb">
                <h3 className="verb-name">
                  <span>03</span>Hollis reads
                </h3>
                <div className="verb-body">
                  <p>
                    The paperwork arrives by email, upload, and forward. It gets
                    sorted, understood, connected to the right entity, and put
                    where you can find it.
                  </p>
                </div>
                <ul className="verb-list">
                  <li>K-1s, 1099s, statements, and capital calls</li>
                  <li>Trust instruments, LPAs, and offering documents</li>
                  <li>Figures extracted to the cent, for your confirmation</li>
                  <li>Search that answers in sentences and cites the page</li>
                </ul>
              </article>

              <article className="verb">
                <h3 className="verb-name">
                  <span>04</span>Hollis drafts
                </h3>
                <div className="verb-body">
                  <p>
                    The work is prepared before you sit down to it. You edit and
                    approve; you do not start from an empty page.
                  </p>
                </div>
                <ul className="verb-list">
                  <li>Client correspondence and meeting follow-ups</li>
                  <li>
                    Investment policy statements from the intake conversation
                  </li>
                  <li>Pre-meeting briefs assembled from the full record</li>
                  <li>Diligence memos separating fact from claim</li>
                </ul>
              </article>

              <article className="verb">
                <h3 className="verb-name">
                  <span>05</span>Hollis proves
                </h3>
                <div className="verb-body">
                  <p>
                    Every figure traces to a source. Every action carries its
                    reasoning. When the examiner asks, the answer is already
                    assembled.
                  </p>
                </div>
                <ul className="verb-list">
                  <li>Immutable double-entry ledger, reversal only</li>
                  <li>Full audit log with the reasoning behind each action</li>
                  <li>
                    Separation of duties and maker-checker on sensitive work
                  </li>
                  <li>Retention policy applied by record class, not by folder</li>
                </ul>
              </article>
            </div>
          </div>
        </section>

        {/* ============ THE LOG ============ */}
        <section className="band wrap" id="log">
          <div
            className="logcard"
            role="region"
            aria-label="Sample morning activity log"
          >
            <div className="logcard-head">
              <h2>This morning</h2>
              <span className="when">Sample log · 07:00–08:15</span>
            </div>
            <ul className="loglist" ref={logRef}>
              <li>
                <span className="stamp">07:03</span>
                <span className="entry">
                  <b>Filed</b> 14 documents and linked them to nine entities.
                  <span className="sub">
                    Three K-1s, one amended trust instrument, ten statements.
                  </span>
                </span>
                <span className="state state-done">Done</span>
              </li>
              <li>
                <span className="stamp">07:15</span>
                <span className="entry">
                  <b>Flagged</b> one allocation breach in a member portfolio.
                  <span className="sub">
                    4.2% over policy band in private credit, first crossed on 28
                    July.
                  </span>
                </span>
                <span className="state state-wait">Needs you</span>
              </li>
              <li>
                <span className="stamp">07:44</span>
                <span className="entry">
                  <b>Drafted</b> three client emails.
                  <span className="sub">
                    Two quarterly follow-ups, one capital call notice.
                  </span>
                </span>
                <span className="state state-wait">Needs you</span>
              </li>
              <li>
                <span className="stamp">08:10</span>
                <span className="entry">
                  <b>Prepared</b> your 9:30 brief.
                  <span className="sub">
                    Four items to raise, with the underlying documents attached.
                  </span>
                </span>
                <span className="state state-done">Ready</span>
              </li>
            </ul>
            <div className="logcard-foot">
              Nothing was sent. Nothing was moved. Every line above is in the
              audit log, with its reasoning.
            </div>
          </div>

          <div className="hero-cta">
            <a className="btn btn-solid btn-lg" href="#contact">
              Start a conversation
            </a>
            <a className="btn btn-quiet btn-lg" href="#asks">
              See where Hollis stops
            </a>
          </div>
        </section>

        {/* ============ WHERE IT STOPS ============ */}
        <section className="band wrap" id="asks">
          <div className="asks">
            <div>
              <h2>And Hollis asks.</h2>
              <p>
                The question every principal asks first is not what the software
                can do. It is what it will do without being told.
              </p>
              <p>
                Nothing leaves the firm and nothing moves money without a person
                approving it. Every action Hollis is permitted to take sits in a
                fixed vocabulary, and every verb in that vocabulary carries a
                tier that determines how far it can go alone.
              </p>
            </div>
            <ul className="gates">
              <li>
                <span className="tier">Tier 1</span>
                <span>
                  Prepared and held. Client communications and money movement
                  wait for a named approver.
                </span>
              </li>
              <li>
                <span className="tier">Tier 2</span>
                <span>
                  Confirmed and logged. Bounded choices, presented before
                  anything is committed.
                </span>
              </li>
              <li>
                <span className="tier">Tier 3</span>
                <span>
                  Runs freely. Reading, reconciling, and assembling — work that
                  changes nothing.
                </span>
              </li>
            </ul>
          </div>
        </section>

        {/* ============ CONTACT ============ */}
        <section className="band band-alt" id="contact">
          <div className="wrap contact">
            <div className="contact-copy">
              <h2>Start a conversation</h2>
              <p>
                Tell us how your firm runs today and where the manual work sits.
                We will show you the parts of it Hollis already handles.
              </p>
              <p className="contact-direct">
                Or write directly —<br />
                <a href="mailto:hello@hollisworks.com">hello@hollisworks.com</a>
              </p>
            </div>

            <form
              className="form"
              id="contactform"
              noValidate
              ref={formRef}
              onSubmit={handleSubmit}
            >
              <div className="field-row">
                <div className="field">
                  <label htmlFor="f-name">Name</label>
                  <input
                    id="f-name"
                    name="name"
                    type="text"
                    autoComplete="name"
                    required
                  />
                </div>
                <div className="field">
                  <label htmlFor="f-firm">Firm</label>
                  <input
                    id="f-firm"
                    name="firm"
                    type="text"
                    autoComplete="organization"
                    required
                  />
                </div>
              </div>
              <div className="field-row">
                <div className="field">
                  <label htmlFor="f-email">Work email</label>
                  <input
                    id="f-email"
                    name="email"
                    type="email"
                    autoComplete="email"
                    required
                  />
                </div>
                <div className="field">
                  <label htmlFor="f-aum">Assets under advisement</label>
                  <select id="f-aum" name="aum" defaultValue="">
                    <option value="">Select</option>
                    <option>Pre-launch</option>
                    <option>Under $100M</option>
                    <option>$100M – $500M</option>
                    <option>$500M – $2B</option>
                    <option>Over $2B</option>
                  </select>
                </div>
              </div>
              <div className="field">
                <label htmlFor="f-note">What takes the most time today?</label>
                <textarea
                  id="f-note"
                  name="note"
                  placeholder="Reconciliation, document handling, client reporting, onboarding, something else."
                />
              </div>
              <div className="form-foot">
                <button
                  className="btn btn-solid btn-lg"
                  type="submit"
                  disabled={submitting}
                >
                  Send
                </button>
                <span className="form-note">
                  We reply within one business day. We do not add you to a list.
                </span>
              </div>
              <p
                className={`form-status${status.state !== "idle" ? " on" : ""}${
                  status.state === "err" ? " err" : ""
                }`}
                id="formstatus"
                role="status"
              >
                {status.message}
              </p>
            </form>
          </div>
        </section>
      </main>

      {/* ============ FOOTER ============ */}
      <footer className="foot">
        <div className="wrap">
          <div className="foot-grid">
            <div>
              <span className="mark">
                Hollisworks<span className="dot">.</span>
              </span>
              <p className="foot-blurb">
                Software for modern registered investment advisers and the
                families they serve.
              </p>
            </div>

            <div>
              <h3>Product</h3>
              <ul>
                <li>
                  <a href="#what">What Hollis does</a>
                </li>
                <li>
                  <a href="#asks">Autonomy and controls</a>
                </li>
                <li>
                  <a href="/security">Security</a>
                </li>
                <li>
                  <a href="/integrations">Integrations</a>
                </li>
                <li>
                  <a href="/status">System status</a>
                </li>
              </ul>
            </div>

            <div>
              <h3>Disclosures</h3>
              <ul>
                <li>
                  <a href="/disclosures">Regulatory disclosures</a>
                </li>
                <li>
                  <a href="/privacy">Privacy policy</a>
                </li>
                <li>
                  <a href="/terms">Terms of service</a>
                </li>
                <li>
                  <a href="/subprocessors">Subprocessors</a>
                </li>
                <li>
                  <a href="/accessibility">Accessibility statement</a>
                </li>
              </ul>
            </div>

            <div>
              <h3>Contact</h3>
              <ul>
                <li>
                  <a href="#contact">Start a conversation</a>
                </li>
                <li>
                  <a href="mailto:hello@hollisworks.com">hello@hollisworks.com</a>
                </li>
                <li>
                  <a href="mailto:security@hollisworks.com">
                    Report a vulnerability
                  </a>
                </li>
                <li>
                  <a href="/firm-search?intent=login">Log in</a>
                </li>
                <li>
                  <a href="/firm-search?intent=enroll">Enroll</a>
                </li>
              </ul>
            </div>
          </div>

          <div className="legal">
            <p>
              Hollisworks provides software to investment advisers and their
              clients. Hollisworks is not an investment adviser, broker-dealer,
              or custodian, and does not provide investment, legal, tax, or
              accounting advice. Nothing on this site is an offer to sell or a
              solicitation of an offer to buy any security.
            </p>
            <p>
              Screens and activity logs shown on this site are illustrative and
              do not depict actual client accounts, holdings, or performance.
              Features described may be in development and are subject to change.
              Availability of custodial and third-party integrations depends on
              the provider and on the advisory firm&rsquo;s own agreements.
            </p>
            <div className="legal-bar">
              <span>© 2026 Hollisworks</span>
              <span className="sep">·</span>
              <a href="/privacy">Privacy</a>
              <span className="sep">·</span>
              <a href="/terms">Terms</a>
              <span className="sep">·</span>
              <a href="/disclosures">Disclosures</a>
              <span className="sep">·</span>
              <a href="/cookies">Cookie preferences</a>
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
}
