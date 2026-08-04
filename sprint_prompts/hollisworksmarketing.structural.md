HOLLISWORKS MARKETING PAGE — integration + Sprint 1 correction +
firm-search + contact form. 5 tasks + verification. Real,
complete HTML/CSS/JS provided by Joe (attached below in full) —
convert and integrate it, do not rewrite or redesign it.

*** THIS RUNS UNATTENDED OVERNIGHT — Joe is NOT available. ***
*** CRITICAL INSTRUCTION, READ THIS FIRST: ***
*** Task 1 asks you to REPORT your discovery findings. This ***
*** means: state them clearly in your response, THEN ***
*** IMMEDIATELY CONTINUE, IN THE SAME RESPONSE, into Task 2, ***
*** Task 3, Task 4, Task 5, and the verification script. ***
*** "Report before proceeding" does NOT mean stop and wait for ***
*** a human to reply — there is no human available tonight. ***
*** A previous attempt at this exact sprint stopped after ***
*** Task 1's report and never built anything — that was WRONG. ***
*** Do not repeat that mistake. Keep working straight through ***
*** all 5 tasks and verification in one continuous run. ***

CONTEXT — Task 1's discovery was ALREADY DONE in a prior attempt.
USE THESE CONFIRMED FINDINGS DIRECTLY, do not re-discover:
  (a) Sprint 1's resolver: GET /api/v1/tenant/resolve ->
      apps/api/routers/tenant.py:21 (thin) delegates to
      resolve_tenant() in apps/api/services/tenant.py:140.
      extract_subdomain() (tenant.py:99) returns None when
      len(parts) < 3 (tenant.py:128) - apex/www yield no
      subdomain. resolve_tenant() pre-seeds org_id=DEFAULT_ORG_ID,
      source="default", resolved=False (tenant.py:153-160), only
      overwritten on a real slug match. THIS is the exact logic
      to change. DEFAULT_ORG_ID is in apps/api/routers/
      entities.py:54. Reserved-slug validation is in tenant.py:
      33-96 (RESERVED_SLUGS includes "admin"). The RLS carve-out
      organizations_preauth_resolve (docs/multitenant1_part1.sql)
      already lets this resolve pre-auth under app_service.
  (b) CRITICAL, CONFIRMED: the FRONTEND DOES NOT CONSUME
      /tenant/resolve AT ALL YET. A repo-wide grep across apps/web
      for "tenant/resolve"/"subdomain" = ZERO hits. proxy.js is
      Auth0-only; layout.js drives branding via loadTheme() ->
      /theme/public, never the resolver. THIS MEANS: "serve the
      marketing page for the bare domain" requires REAL, NEW
      frontend wiring to actually call/consume the resolver's
      result - it is NOT just flipping an existing backend
      boolean. Build this real connection in Task 3, do not
      assume it already exists.
  (c) organizations now has login_url, enroll_url (text,
      nullable), AND saml_connection_name (text, nullable,
      unrelated to this sprint, added separately - ignore it,
      just don't break it) - confirmed live in the DB already.

STANDING RULES: org_id never from request body; no interactive
prompts (none are possible tonight); DO NOT alter the provided
design (colors/fonts/copy/layout) — convert it faithfully.

<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hollisworks — AI orchestration for the modern RIA</title>
<meta name="description" content="Hollis maps, watches, reads, drafts, and proves. AI orchestration for the modern RIA.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Libre+Caslon+Display&family=Libre+Caslon+Text:ital,wght@0,400;0,700;1,400&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;450;500;600&display=swap" rel="stylesheet">
<style>
/* ============================================================
   HOLLISWORKS — brand tokens
   NOTE: every value below is a seed default. In product these
   resolve from org_settings (brand.*), never hardcoded.
   ============================================================ */
:root{
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
}

*,*::before,*::after{ box-sizing:border-box; }

html{ scroll-behavior:smooth; }
@media (prefers-reduced-motion: reduce){ html{ scroll-behavior:auto; } }

body{
  margin:0;
  background:var(--paper);
  color:var(--ink);
  font-family:var(--sans);
  font-size:17px;
  line-height:1.6;
  -webkit-font-smoothing:antialiased;
  text-rendering:optimizeLegibility;
}

.wrap{ max-width:var(--maxw); margin:0 auto; padding-inline:var(--gutter); }

a{ color:inherit; }

:focus-visible{
  outline:2px solid var(--holly);
  outline-offset:3px;
  border-radius:2px;
}

.skip{
  position:absolute; left:-9999px; top:0;
  background:var(--holly); color:#fff;
  padding:.75rem 1.25rem; z-index:100;
  font-size:.9rem; text-decoration:none;
}
.skip:focus{ left:.5rem; top:.5rem; }

/* ---------------- header ---------------- */
.masthead{
  position:sticky; top:0; z-index:50;
  background:rgba(251,250,248,.88);
  backdrop-filter:saturate(180%) blur(12px);
  border-bottom:1px solid var(--rule-soft);
}
.masthead-in{
  display:flex; align-items:center; gap:1.5rem;
  min-height:74px;
}
.mark{
  font-family:var(--display);
  font-size:1.45rem;
  letter-spacing:-.01em;
  color:var(--holly);
  text-decoration:none;
  white-space:nowrap;
}
.mark .dot{ color:var(--bronze); }

.mainnav{
  display:flex; gap:1.75rem;
  margin-left:auto;
  font-size:.9rem;
}
.mainnav a{
  text-decoration:none;
  color:var(--ink-2);
  padding-block:.35rem;
  border-bottom:1px solid transparent;
  transition:color .18s ease, border-color .18s ease;
}
.mainnav a:hover{ color:var(--holly); border-bottom-color:var(--bronze); }

.authset{ display:flex; align-items:center; gap:.6rem; }

.btn{
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
.btn-quiet{ color:var(--ink-2); border-color:var(--rule); background:transparent; }
.btn-quiet:hover{ color:var(--holly); border-color:var(--holly); }
.btn-solid{ background:var(--holly); color:#F7F9F7; }
.btn-solid:hover{ background:var(--holly-deep); }
.btn-lg{ font-size:1rem; padding:.85rem 1.6rem; }

.navtoggle{
  display:none; margin-left:auto;
  background:none; border:1px solid var(--rule);
  border-radius:3px; padding:.5rem .7rem;
  font-family:var(--sans); font-size:.85rem; color:var(--ink-2);
  cursor:pointer;
}

/* ---------------- hero ---------------- */
.hero{ padding-block:clamp(3.5rem,8vw,6rem) clamp(2.5rem,5vw,4rem); }
.eyebrow{
  font-family:var(--mono);
  font-size:.72rem;
  letter-spacing:.14em;
  text-transform:uppercase;
  color:var(--ink-3);
  margin:0 0 1.5rem;
}
.hero h1{
  font-family:var(--display);
  font-weight:400;
  font-size:clamp(2.3rem, 4.6vw, 3.5rem);
  line-height:1.08;
  letter-spacing:-.015em;
  margin:0 0 1.4rem;
  max-width:20ch;
}
.lede{
  font-family:var(--text);
  font-size:clamp(1.1rem,2vw,1.32rem);
  line-height:1.55;
  color:var(--ink-2);
  max-width:46ch;
  margin:0 0 2.25rem;
}
.hero-cta{ display:flex; flex-wrap:wrap; gap:.85rem; align-items:center; margin-top:clamp(1.75rem,3.5vw,2.5rem); }
.hero-note{
  font-family:var(--mono); font-size:.78rem;
  color:var(--ink-3); margin-left:.35rem;
}

/* ---------------- the log (signature) ---------------- */
.logcard{
  background:var(--surface);
  border:1px solid var(--rule);
  border-radius:4px;
  box-shadow:0 1px 2px rgba(22,33,28,.03), 0 12px 32px -18px rgba(22,33,28,.18);
  overflow:hidden;
}
.logcard-head{
  display:flex; align-items:baseline; gap:1rem; flex-wrap:wrap;
  padding:1.15rem clamp(1.1rem,3vw,2rem);
  border-bottom:1px solid var(--rule-soft);
  background:linear-gradient(to bottom, #FDFCFA, var(--surface));
}
.logcard-head h2{
  font-family:var(--display); font-weight:400;
  font-size:1.15rem; margin:0; letter-spacing:.01em;
}
.logcard-head .when{
  font-family:var(--mono); font-size:.75rem;
  color:var(--ink-3); margin-left:auto;
}
.loglist{ list-style:none; margin:0; padding:.4rem 0; }
.loglist li{
  display:grid;
  grid-template-columns:auto 1fr auto;
  gap:.35rem 1.25rem;
  align-items:baseline;
  padding:.85rem clamp(1.1rem,3vw,2rem);
  border-bottom:1px solid var(--rule-soft);
}
.loglist li:last-child{ border-bottom:0; }
.stamp{
  font-family:var(--mono); font-size:.76rem;
  color:var(--ink-3); font-variant-numeric:tabular-nums;
}
.entry{ font-size:.98rem; color:var(--ink); line-height:1.5; }
.entry b{ font-weight:600; }
.entry .sub{ display:block; color:var(--ink-3); font-size:.88rem; margin-top:.15rem; }

.state{
  font-family:var(--mono);
  font-size:.68rem; letter-spacing:.1em; text-transform:uppercase;
  padding:.22rem .55rem; border-radius:2px; white-space:nowrap;
}
.state-done{ background:var(--holly-tint); color:var(--holly); }
.state-wait{ background:var(--bronze-tint); color:var(--bronze); }

.logcard-foot{
  padding:1rem clamp(1.1rem,3vw,2rem);
  background:var(--paper-alt);
  border-top:1px solid var(--rule-soft);
  font-family:var(--text); font-style:italic;
  font-size:.95rem; color:var(--ink-2);
}

/* staggered arrival */
.loglist li{ opacity:0; transform:translateY(6px); }
.loglist.in li{ animation:arrive .5s ease forwards; }
.loglist.in li:nth-child(1){ animation-delay:.05s }
.loglist.in li:nth-child(2){ animation-delay:.28s }
.loglist.in li:nth-child(3){ animation-delay:.51s }
.loglist.in li:nth-child(4){ animation-delay:.74s }
@keyframes arrive{ to{ opacity:1; transform:none; } }
@media (prefers-reduced-motion: reduce){
  .loglist li, .loglist.in li{ opacity:1; transform:none; animation:none; }
}

/* ---------------- verbs ---------------- */
.band{ padding-block:clamp(3.5rem,7vw,5.5rem); }
.band-alt{ background:var(--paper-alt); border-block:1px solid var(--rule-soft); }

.band-intro{ max-width:52ch; margin-bottom:clamp(2.5rem,5vw,3.5rem); }
.band-intro h2{
  font-family:var(--display); font-weight:400;
  font-size:clamp(1.9rem,4vw,2.7rem);
  line-height:1.15; letter-spacing:-.015em; margin:0 0 .85rem;
}
.band-intro p{
  font-family:var(--text); font-size:1.1rem;
  color:var(--ink-2); margin:0;
}

.verbs{ display:grid; gap:0; border-top:1px solid var(--rule); }
.verb{
  display:grid;
  grid-template-columns:minmax(0,7.5rem) minmax(0,1fr) minmax(0,1.05fr);
  gap:1rem clamp(1.5rem,4vw,3.5rem);
  padding-block:clamp(1.75rem,3.5vw,2.5rem);
  border-bottom:1px solid var(--rule);
  align-items:start;
}
.verb-name{
  font-family:var(--display); font-weight:400;
  font-size:clamp(1.5rem,3vw,2rem);
  line-height:1.05; letter-spacing:-.01em;
  color:var(--holly);
}
.verb-name span{
  display:block;
  font-family:var(--mono); font-size:.68rem;
  letter-spacing:.14em; text-transform:uppercase;
  color:var(--ink-3); margin-bottom:.5rem;
}
.verb-body p{
  font-family:var(--text); font-size:1.06rem;
  line-height:1.55; margin:0; color:var(--ink);
}
.verb-list{
  list-style:none; margin:0; padding:0;
  font-family:var(--sans); font-size:.9rem; color:var(--ink-2);
}
.verb-list li{
  padding:.42rem 0 .42rem 1.1rem;
  border-bottom:1px solid var(--rule-soft);
  position:relative;
}
.verb-list li:last-child{ border-bottom:0; }
.verb-list li::before{
  content:""; position:absolute; left:0; top:1.05em;
  width:5px; height:1px; background:var(--bronze);
}

/* ---------------- asks (boundary) ---------------- */
.asks{
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
.asks h2{
  font-family:var(--display); font-weight:400;
  font-size:clamp(1.8rem,3.6vw,2.5rem);
  line-height:1.12; margin:0 0 .9rem; color:var(--holly-deep);
}
.asks p{
  font-family:var(--text); font-size:1.08rem;
  line-height:1.6; color:var(--ink-2); margin:0 0 1rem;
}
.asks p:last-child{ margin-bottom:0; }
.gates{ list-style:none; margin:0; padding:0; }
.gates li{
  background:var(--surface);
  border:1px solid #DDE6E0;
  border-radius:3px;
  padding:.85rem 1rem;
  margin-bottom:.6rem;
  font-size:.92rem;
  display:flex; gap:.85rem; align-items:baseline;
}
.gates li:last-child{ margin-bottom:0; }
.tier{
  font-family:var(--mono); font-size:.66rem;
  letter-spacing:.1em; text-transform:uppercase;
  color:var(--ink-3); white-space:nowrap;
  min-width:3.6rem;
}

/* ---------------- contact ---------------- */
.contact{
  display:grid;
  grid-template-columns:minmax(0,.85fr) minmax(0,1fr);
  gap:clamp(2rem,5vw,4.5rem);
  align-items:start;
}
.contact-copy h2{
  font-family:var(--display); font-weight:400;
  font-size:clamp(1.9rem,4vw,2.7rem);
  line-height:1.12; margin:0 0 .9rem; letter-spacing:-.015em;
}
.contact-copy p{
  font-family:var(--text); font-size:1.08rem;
  color:var(--ink-2); margin:0 0 1.5rem;
}
.contact-direct{
  font-family:var(--mono); font-size:.85rem;
  color:var(--ink-2); padding-top:1.25rem;
  border-top:1px solid var(--rule);
}
.contact-direct a{ color:var(--holly); }

.form{
  background:var(--surface);
  border:1px solid var(--rule);
  border-radius:4px;
  padding:clamp(1.5rem,3.5vw,2.25rem);
  box-shadow:0 12px 32px -22px rgba(22,33,28,.22);
}
.field{ margin-bottom:1.1rem; }
.field label{
  display:block;
  font-family:var(--mono); font-size:.7rem;
  letter-spacing:.12em; text-transform:uppercase;
  color:var(--ink-3); margin-bottom:.45rem;
}
.field input, .field select, .field textarea{
  width:100%;
  font-family:var(--sans); font-size:.95rem; color:var(--ink);
  background:var(--paper);
  border:1px solid var(--rule);
  border-radius:3px;
  padding:.7rem .8rem;
  transition:border-color .18s ease, background .18s ease;
}
.field textarea{ min-height:112px; resize:vertical; }
.field input:focus, .field select:focus, .field textarea:focus{
  background:var(--surface); border-color:var(--holly); outline:none;
}
.field-row{ display:grid; grid-template-columns:1fr 1fr; gap:1.1rem; }
.form-foot{
  display:flex; align-items:center; gap:1rem; flex-wrap:wrap;
  margin-top:1.35rem; padding-top:1.35rem;
  border-top:1px solid var(--rule-soft);
}
.form-note{ font-size:.8rem; color:var(--ink-3); max-width:30ch; }
.form-status{
  margin-top:1rem; font-family:var(--mono); font-size:.8rem;
  color:var(--holly); display:none;
}
.form-status.on{ display:block; }

/* ---------------- footer ---------------- */
.foot{
  background:var(--holly-deep);
  color:#C6D2CA;
  padding-block:clamp(3rem,6vw,4.5rem) 2rem;
  margin-top:clamp(3.5rem,7vw,5.5rem);
}
.foot-grid{
  display:grid;
  grid-template-columns:minmax(0,1.4fr) repeat(3, minmax(0,1fr));
  gap:clamp(1.75rem,4vw,3rem);
  padding-bottom:2.5rem;
  border-bottom:1px solid rgba(255,255,255,.12);
}
.foot .mark{ color:#F2F6F3; font-size:1.35rem; }
.foot .mark .dot{ color:var(--bronze); }
.foot-blurb{
  font-family:var(--text); font-size:.95rem;
  color:#9FB0A6; margin:.9rem 0 0; max-width:34ch; line-height:1.55;
}
.foot h3{
  font-family:var(--mono); font-size:.68rem;
  letter-spacing:.14em; text-transform:uppercase;
  color:#7E9187; margin:0 0 1rem; font-weight:500;
}
.foot ul{ list-style:none; margin:0; padding:0; }
.foot li{ margin-bottom:.6rem; }
.foot a{
  color:#C6D2CA; text-decoration:none; font-size:.9rem;
  border-bottom:1px solid transparent;
  transition:color .18s ease, border-color .18s ease;
}
.foot a:hover{ color:#F2F6F3; border-bottom-color:var(--bronze); }

.legal{ padding-top:2rem; }
.legal p{
  font-size:.79rem; line-height:1.65;
  color:#84978C;
  margin:0 0 .9rem; max-width:88ch;
}
.legal-bar{
  display:flex; flex-wrap:wrap; gap:.5rem 1.5rem;
  align-items:center; margin-top:1.5rem;
  font-size:.79rem; color:#7E9187;
}
.legal-bar a{ font-size:.79rem; color:#9FB0A6; }
.legal-bar .sep{ color:#4E6157; }

/* ---------------- responsive ---------------- */
@media (max-width: 900px){
  .verb{ grid-template-columns:1fr; gap:.85rem; }
  .verb-name{ display:flex; align-items:baseline; gap:.85rem; }
  .verb-name span{ margin-bottom:0; }
  .asks{ grid-template-columns:1fr; }
  .contact{ grid-template-columns:1fr; }
  .foot-grid{ grid-template-columns:1fr 1fr; }
}
@media (max-width: 720px){
  .mainnav{
    display:none; position:absolute; top:74px; left:0; right:0;
    background:var(--surface); border-bottom:1px solid var(--rule);
    flex-direction:column; gap:0; padding:.5rem var(--gutter) 1rem;
  }
  .mainnav.open{ display:flex; }
  .mainnav a{ padding-block:.7rem; border-bottom:1px solid var(--rule-soft); }
  .navtoggle{ display:block; }
  .authset{ margin-left:.5rem; }
  .btn{ padding:.55rem .85rem; font-size:.85rem; }
  .loglist li{ grid-template-columns:1fr auto; }
  .loglist .stamp{ grid-column:1 / -1; }
  .field-row{ grid-template-columns:1fr; }
  .foot-grid{ grid-template-columns:1fr; }
}
</style>
</head>
<body>

<a class="skip" href="#main">Skip to content</a>

<header class="masthead">
  <div class="wrap masthead-in">
    <a class="mark" href="#top">Hollisworks<span class="dot">.</span></a>
    <button class="navtoggle" id="navtoggle" aria-expanded="false" aria-controls="mainnav">Menu</button>
    <nav class="mainnav" id="mainnav" aria-label="Main">
      <a href="#what">What Hollis does</a>
      <a href="#asks">Where it stops</a>
      <a href="#contact">Contact</a>
    </nav>
    <div class="authset">
      <a class="btn btn-quiet" href="/login">Log in</a>
      <a class="btn btn-solid" href="/enroll">Enroll</a>
    </div>
  </div>
</header>

<main id="main">

  <!-- ============ HERO ============ -->
  <section class="hero wrap" id="top">
    <p class="eyebrow">AI orchestration for the modern RIA</p>
    <h1>Hollis works. For you.</h1>
    <p class="lede">
      Hollis orchestrates your operations and client service — reconciling the book,
      handling the paperwork, and preparing client work before you sit down to it.
      Your team spends its hours on advice rather than administration.
    </p>
  </section>

  <!-- ============ THE FIVE VERBS ============ -->
  <section class="band band-alt" id="what">
    <div class="wrap">
      <div class="band-intro">
        <h2>What Hollis can do</h2>
        <p>Not modules you have to wire together. Work that happens on your book, whether or not you asked this morning.</p>
      </div>

      <div class="verbs">

        <article class="verb">
          <h3 class="verb-name"><span>01</span>Hollis maps</h3>
          <div class="verb-body">
            <p>Every entity, every ownership percentage, every layer of look-through — and
            what all of it looked like on any date you name.</p>
          </div>
          <ul class="verb-list">
            <li>Bi-temporal ownership graph with as-of restatement</li>
            <li>Look-through to ultimate beneficial exposure</li>
            <li>Trusts, holdcos, SPVs, and classes in one structure</li>
            <li>Time travel to any prior position of the map</li>
          </ul>
        </article>

        <article class="verb">
          <h3 class="verb-name"><span>02</span>Hollis watches</h3>
          <div class="verb-body">
            <p>Drift, breach, and stall — caught when they happen rather than at quarterly
            review. On the book and on the business.</p>
          </div>
          <ul class="verb-list">
            <li>Allocation drift against policy bands</li>
            <li>Investment policy breaches, with the date first crossed</li>
            <li>Pipeline aging and stage stalls</li>
            <li>Revenue and client profitability against forecast</li>
          </ul>
        </article>

        <article class="verb">
          <h3 class="verb-name"><span>03</span>Hollis reads</h3>
          <div class="verb-body">
            <p>The paperwork arrives by email, upload, and forward. It gets sorted,
            understood, connected to the right entity, and put where you can find it.</p>
          </div>
          <ul class="verb-list">
            <li>K-1s, 1099s, statements, and capital calls</li>
            <li>Trust instruments, LPAs, and offering documents</li>
            <li>Figures extracted to the cent, for your confirmation</li>
            <li>Search that answers in sentences and cites the page</li>
          </ul>
        </article>

        <article class="verb">
          <h3 class="verb-name"><span>04</span>Hollis drafts</h3>
          <div class="verb-body">
            <p>The work is prepared before you sit down to it. You edit and approve;
            you do not start from an empty page.</p>
          </div>
          <ul class="verb-list">
            <li>Client correspondence and meeting follow-ups</li>
            <li>Investment policy statements from the intake conversation</li>
            <li>Pre-meeting briefs assembled from the full record</li>
            <li>Diligence memos separating fact from claim</li>
          </ul>
        </article>

        <article class="verb">
          <h3 class="verb-name"><span>05</span>Hollis proves</h3>
          <div class="verb-body">
            <p>Every figure traces to a source. Every action carries its reasoning.
            When the examiner asks, the answer is already assembled.</p>
          </div>
          <ul class="verb-list">
            <li>Immutable double-entry ledger, reversal only</li>
            <li>Full audit log with the reasoning behind each action</li>
            <li>Separation of duties and maker-checker on sensitive work</li>
            <li>Retention policy applied by record class, not by folder</li>
          </ul>
        </article>

      </div>
    </div>
  </section>

  <!-- ============ THE LOG ============ -->
  <section class="band wrap" id="log">
    <div class="logcard" role="region" aria-label="Sample morning activity log">
      <div class="logcard-head">
        <h2>This morning</h2>
        <span class="when">Sample log &middot; 07:00&ndash;08:15</span>
      </div>
      <ul class="loglist">
        <li>
          <span class="stamp">07:03</span>
          <span class="entry"><b>Filed</b> 14 documents and linked them to nine entities.
            <span class="sub">Three K-1s, one amended trust instrument, ten statements.</span></span>
          <span class="state state-done">Done</span>
        </li>
        <li>
          <span class="stamp">07:15</span>
          <span class="entry"><b>Flagged</b> one allocation breach in a member portfolio.
            <span class="sub">4.2% over policy band in private credit, first crossed on 28 July.</span></span>
          <span class="state state-wait">Needs you</span>
        </li>
        <li>
          <span class="stamp">07:44</span>
          <span class="entry"><b>Drafted</b> three client emails.
            <span class="sub">Two quarterly follow-ups, one capital call notice.</span></span>
          <span class="state state-wait">Needs you</span>
        </li>
        <li>
          <span class="stamp">08:10</span>
          <span class="entry"><b>Prepared</b> your 9:30 brief.
            <span class="sub">Four items to raise, with the underlying documents attached.</span></span>
          <span class="state state-done">Ready</span>
        </li>
      </ul>
      <div class="logcard-foot">
        Nothing was sent. Nothing was moved. Every line above is in the audit log, with its reasoning.
      </div>
    </div>

    <div class="hero-cta">
      <a class="btn btn-solid btn-lg" href="#contact">Start a conversation</a>
      <a class="btn btn-quiet btn-lg" href="#asks">See where Hollis stops</a>
    </div>
  </section>

  <!-- ============ WHERE IT STOPS ============ -->
  <section class="band wrap" id="asks">
    <div class="asks">
      <div>
        <h2>And Hollis asks.</h2>
        <p>
          The question every principal asks first is not what the software can do.
          It is what it will do without being told.
        </p>
        <p>
          Nothing leaves the firm and nothing moves money without a person approving it.
          Every action Hollis is permitted to take sits in a fixed vocabulary, and every
          verb in that vocabulary carries a tier that determines how far it can go alone.
        </p>
      </div>
      <ul class="gates">
        <li><span class="tier">Tier 1</span><span>Prepared and held. Client communications and money movement wait for a named approver.</span></li>
        <li><span class="tier">Tier 2</span><span>Confirmed and logged. Bounded choices, presented before anything is committed.</span></li>
        <li><span class="tier">Tier 3</span><span>Runs freely. Reading, reconciling, and assembling — work that changes nothing.</span></li>
      </ul>
    </div>
  </section>

  <!-- ============ CONTACT ============ -->
  <section class="band band-alt" id="contact">
    <div class="wrap contact">
      <div class="contact-copy">
        <h2>Start a conversation</h2>
        <p>
          Tell us how your firm runs today and where the manual work sits.
          We will show you the parts of it Hollis already handles.
        </p>
        <p class="contact-direct">
          Or write directly —<br>
          <a href="mailto:hello@hollisworks.com">hello@hollisworks.com</a>
        </p>
      </div>

      <form class="form" id="contactform" novalidate>
        <div class="field-row">
          <div class="field">
            <label for="f-name">Name</label>
            <input id="f-name" name="name" type="text" autocomplete="name" required>
          </div>
          <div class="field">
            <label for="f-firm">Firm</label>
            <input id="f-firm" name="firm" type="text" autocomplete="organization" required>
          </div>
        </div>
        <div class="field-row">
          <div class="field">
            <label for="f-email">Work email</label>
            <input id="f-email" name="email" type="email" autocomplete="email" required>
          </div>
          <div class="field">
            <label for="f-aum">Assets under advisement</label>
            <select id="f-aum" name="aum">
              <option value="">Select</option>
              <option>Pre-launch</option>
              <option>Under $100M</option>
              <option>$100M – $500M</option>
              <option>$500M – $2B</option>
              <option>Over $2B</option>
            </select>
          </div>
        </div>
        <div class="field">
          <label for="f-note">What takes the most time today?</label>
          <textarea id="f-note" name="note" placeholder="Reconciliation, document handling, client reporting, onboarding, something else."></textarea>
        </div>
        <div class="form-foot">
          <button class="btn btn-solid btn-lg" type="submit">Send</button>
          <span class="form-note">We reply within one business day. We do not add you to a list.</span>
        </div>
        <p class="form-status" id="formstatus" role="status">Thank you — your note is on its way.</p>
      </form>
    </div>
  </section>

</main>

<!-- ============ FOOTER ============ -->
<footer class="foot">
  <div class="wrap">
    <div class="foot-grid">
      <div>
        <span class="mark">Hollisworks<span class="dot">.</span></span>
        <p class="foot-blurb">
          Software for modern registered investment advisers and the families they serve.
        </p>
      </div>

      <div>
        <h3>Product</h3>
        <ul>
          <li><a href="#what">What Hollis does</a></li>
          <li><a href="#asks">Autonomy and controls</a></li>
          <li><a href="/security">Security</a></li>
          <li><a href="/integrations">Integrations</a></li>
          <li><a href="/status">System status</a></li>
        </ul>
      </div>

      <div>
        <h3>Disclosures</h3>
        <ul>
          <li><a href="/disclosures">Regulatory disclosures</a></li>
          <li><a href="/privacy">Privacy policy</a></li>
          <li><a href="/terms">Terms of service</a></li>
          <li><a href="/subprocessors">Subprocessors</a></li>
          <li><a href="/accessibility">Accessibility statement</a></li>
        </ul>
      </div>

      <div>
        <h3>Contact</h3>
        <ul>
          <li><a href="#contact">Start a conversation</a></li>
          <li><a href="mailto:hello@hollisworks.com">hello@hollisworks.com</a></li>
          <li><a href="mailto:security@hollisworks.com">Report a vulnerability</a></li>
          <li><a href="/login">Log in</a></li>
          <li><a href="/enroll">Enroll</a></li>
        </ul>
      </div>
    </div>

    <div class="legal">
      <p>
        Hollisworks provides software to investment advisers and their clients. Hollisworks is not an
        investment adviser, broker-dealer, or custodian, and does not provide investment, legal, tax,
        or accounting advice. Nothing on this site is an offer to sell or a solicitation of an offer to
        buy any security.
      </p>
      <p>
        Screens and activity logs shown on this site are illustrative and do not depict actual client
        accounts, holdings, or performance. Features described may be in development and are subject to
        change. Availability of custodial and third-party integrations depends on the provider and on the
        advisory firm's own agreements.
      </p>
      <div class="legal-bar">
        <span>© 2026 Hollisworks</span>
        <span class="sep">·</span>
        <a href="/privacy">Privacy</a>
        <span class="sep">·</span>
        <a href="/terms">Terms</a>
        <span class="sep">·</span>
        <a href="/disclosures">Disclosures</a>
        <span class="sep">·</span>
        <a href="/cookies">Cookie preferences</a>
      </div>
    </div>
  </div>
</footer>

<script>
(function(){
  var t = document.getElementById('navtoggle');
  var n = document.getElementById('mainnav');
  t.addEventListener('click', function(){
    var open = n.classList.toggle('open');
    t.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
  n.addEventListener('click', function(e){
    if(e.target.tagName === 'A'){ n.classList.remove('open'); t.setAttribute('aria-expanded','false'); }
  });

  var log = document.querySelector('.loglist');
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if(log){
    if(reduce || !('IntersectionObserver' in window)){ log.classList.add('in'); }
    else {
      var io = new IntersectionObserver(function(es){
        es.forEach(function(e){ if(e.isIntersecting){ log.classList.add('in'); io.disconnect(); } });
      }, { threshold:.25 });
      io.observe(log);
    }
  }

  var f = document.getElementById('contactform');
  var s = document.getElementById('formstatus');
  f.addEventListener('submit', function(e){
    e.preventDefault();
    if(!f.checkValidity()){ f.reportValidity(); return; }
    // Wire to POST /api/v1/marketing/contact
    s.classList.add('on');
    f.querySelector('button[type=submit]').disabled = true;
  });
})();
</script>

</body>
</html>

=== TASK 1: Confirm the above findings still hold, briefly ===
Do a QUICK re-check (not a full re-discovery) that (a)/(b)/(c)
above are still accurate against the current real code - things
may have changed since the prior attempt. If anything differs
meaningfully, note it, but DO NOT STOP — proceed into Task 2
regardless, adjusting your approach based on what you find.

=== TASK 2: Convert + integrate the marketing page ===
Convert the provided HTML/CSS/JS into a real page in this
Next.js app, served specifically for requests to the bare
hollisworks.com / www.hollisworks.com domains (no subdomain).
Since the frontend doesn't call the resolver yet (finding b):
build the real connection — the page/layout logic that calls
GET /api/v1/tenant/resolve (or replicates equivalent logic
client/server-side, your judgment on the cleanest real
implementation) and renders this marketing page specifically
when the result indicates the default/no-subdomain case.
Preserve the design exactly — fonts, colors, layout, copy, the
animated activity log, all of it.

=== TASK 3: Sprint 1 correction — fix the fallback, for real ===
Change resolve_tenant()'s actual behavior per finding (a): the
default/no-subdomain case should now correctly signal "show the
Hollisworks marketing page" (wired to Task 2) rather than
silently resolving to 2nd Act's DEFAULT_ORG_ID as if that were a
real tenant match. 2nd Act's app remains fully reachable via
2ndactcapital.hollisworks.com — prove this specifically still
works (a real regression check).

=== TASK 4: Shared firm-search interstitial (Login AND Enroll
both use it) ===
  - BOTH the "Log in" and "Enroll" buttons on the Hollisworks
    marketing page route to the SAME interstitial page — a
    firm-name search field — but each REMEMBERS which button was
    originally clicked (e.g. via a query param like ?intent=login
    or ?intent=enroll).
  - On submit: fuzzy-match the entered name against
    organizations.name (NOT slug). On a confident match: redirect
    to that org's REAL stored login_url or enroll_url, based on
    the original intent.
  - On AMBIGUOUS or NO match: ask the user to clarify or retry —
    do NOT show a pick-list, do NOT guess/take the closest match.
  - SPECIAL CASE: if the entered name matches "Hollisworks"
    itself, redirect to admin.hollisworks.com's login or enroll
    path (per intent) — an explicit, narrow special case in the
    matching logic, NOT a seed row in organizations. If no real
    page exists yet at that destination, wire the CORRECT redirect
    target anyway and report clearly that the destination itself
    is a placeholder pending later work — do not let this block
    finishing the rest of the sprint.

=== TASK 5: Real contact-form endpoint ===
Build the actual POST /api/v1/marketing/contact endpoint the
form's own JS already expects — store the submission (a simple
new table is fine, org_id not applicable pre-tenant), wire the
frontend to call it for real instead of faking success.

=== VERIFICATION ===
Write verify_hollisworksmarketing.py (apps/api/scripts/) —
pass/fail only, no interactive prompts, teardown-at-start and
teardown-at-end. THIS FILE MUST EXIST when you finish — a prior
attempt failed specifically because this was never created.

Assertions:
  [Y] Report findings (a)/(b)/(c) confirmation from Task 1
  [Y] A request to hollisworks.com (no subdomain) renders the
      Hollisworks marketing page, NOT 2nd Act's app
  [Y] A request to 2ndactcapital.hollisworks.com still correctly
      renders 2nd Act's app — the regression check
  [Y] Firm-search with intent=login: a real seeded org name
      produces a correct redirect to its REAL STORED login_url
  [Y] Firm-search with intent=enroll: same org, correct redirect
      to its REAL STORED enroll_url
  [Y] Firm-search: ambiguous/no-match asks to clarify/retry — no
      pick-list, no guessing
  [Y] Firm-search: "Hollisworks" redirects to admin.hollisworks.
      com's correct login/enroll path per intent
  [Y] A real contact-form submission is genuinely persisted
  [Y] npm run build exits 0
  [Y] No hardcoded Signature-palette (2nd Act) hex — this page
      has its OWN brand tokens (holly/bronze/paper)
  [Y] Teardown: zero leftover rows

Report each assertion explicitly. Push when 100% pass — hold for
manual review regardless of tier. IF YOU FINISH DISCOVERY AND
FEEL UNCERTAIN WHETHER TO CONTINUE: CONTINUE. There is no one
to ask tonight. Make a reasonable decision and keep building.
