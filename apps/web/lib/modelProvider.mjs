// REP15 — platform-published models and tenant subscriptions: the pure logic behind Replication → Model catalog
// (tenant) and Platform → Published models (the provider). Every control is decided HERE from the server's envelope
// and FAILS CLOSED: a control shows only when the server's own permission flag is exactly `true` AND the server's
// vocabulary lists the action (a missing, partial or malformed envelope shows none — never a client default). A
// closed publishing gate shows no publish / subscribe / adopt control at all, only the server's own refusal text.
// Labels come from the envelope's vocabularies; figures are shown exactly as the server states them (strings).

const isTrue = (v) => v === true;
const listOf = (x) => (Array.isArray(x) ? x : []);
const obj = (x) => (x && typeof x === "object" && !Array.isArray(x) ? x : {});

export function perms(envelope) {
  return obj(obj(envelope).permissions);
}

export function vocab(envelope) {
  return obj(obj(envelope).vocabularies);
}

export function labelOf(list, key) {
  if (!Array.isArray(list)) return key ?? "";
  const hit = list.find((x) => x && x.key === key);
  return hit ? hit.label : (key ?? "");
}

const editable = (env) => listOf(vocab(env).editable);
const platformEditable = (env) => listOf(vocab(env).platform_editable);

/** The gate's refusal, verbatim from the server (never a client-side copy). */
export function refusalText(envelope) {
  const v = vocab(envelope).refusal;
  return typeof v === "string" ? v : "";
}

/** Tenant controls for one catalog item. Subscribing needs can_admin + "subscribe", a subscribable model, no open
 * subscription, and the agreement in force acknowledged (current). Acknowledging needs can_admin + an agreement in
 * force that is not yet acknowledged. */
export function catalogControls(envelope, item) {
  const p = perms(envelope);
  const it = obj(item);
  const ag = obj(it.agreement);
  const admin = isTrue(p.can_admin) && editable(envelope).includes("subscribe");
  const open = it.subscription && ["requested", "active", "suspended"].includes(obj(it.subscription).status);
  return {
    acknowledge: admin && isTrue(it.subscribable) && !open && isTrue(ag.in_force) && !isTrue(ag.current),
    subscribe: admin && isTrue(it.subscribable) && !open && isTrue(ag.in_force) && isTrue(ag.current),
  };
}

/** Tenant controls for one subscription. Adopt / copy / refresh need can_write + the action AND an ACTIVE
 * subscription; ending needs can_admin + "end" and a subscription that is not final. */
export function subscriptionControls(envelope, sub) {
  const p = perms(envelope);
  const s = obj(sub);
  const w = isTrue(p.can_write);
  const active = s.status === "active";
  const pending = listOf(obj(s.model).pending).filter((v) => obj(v).status === "draft");
  return {
    adopt: w && active && editable(envelope).includes("adopt") && pending.length > 0,
    adoptVersions: w && active && editable(envelope).includes("adopt") ? pending.map((v) => v.version_id) : [],
    copy: w && active && editable(envelope).includes("copy"),
    refresh: w && active && editable(envelope).includes("refresh"),
    end: isTrue(p.can_admin) && editable(envelope).includes("end") && ["requested", "active", "suspended"].includes(s.status),
  };
}

/** The provider (platform) screen. Every control needs is_super_admin === true AND the platform action listed; a
 * gate-dependent control (publish / decide) additionally needs the gate open. Opening needs every readiness item
 * met (the server re-checks; the database refuses otherwise). */
export function providerControls(envelope) {
  const p = perms(envelope);
  const sa = isTrue(p.is_super_admin);
  const pe = platformEditable(envelope);
  const e = obj(envelope);
  const gateOpen = isTrue(obj(e.gate).is_open);
  const ready = isTrue(e.ready_to_open);
  return {
    saveProvider: sa && pe.includes("provider"),
    openGate: sa && pe.includes("gate") && !gateOpen && ready,
    closeGate: sa && pe.includes("gate") && gateOpen,
    publish: sa && pe.includes("publish") && gateOpen,
    decide: sa && pe.includes("decide") && gateOpen,
    retire: sa && pe.includes("retire"),
    subscriptions: sa && pe.includes("subscriptions"),
    fees: sa && pe.includes("fees") && gateOpen,
  };
}

/** Readiness lines exactly as the server states them. */
export function readinessLines(envelope) {
  return listOf(obj(envelope).readiness).map((i) => ({ key: i.key, label: i.label, met: i.met === true }));
}

/** A "new version available" diff, as the server computed it: each row's before / after / change strings untouched,
 * with a sign label. */
export function diffRows(diff) {
  return listOf(diff).map((r) => {
    const c = String(r.change ?? "0");
    return { key: r.key, label: r.ticker || r.name || r.key, before: String(r.before), after: String(r.after), change: c,
      direction: c.startsWith("-") ? "down" : c === "0" ? "same" : "up", kind: r.kind };
  });
}

/** What the provider sees of a tenant: status and aggregates only. Any other field the server ever sent is dropped
 * here too (defence in depth for the isolation rule). */
export const SUBSCRIBER_FIELDS = ["subscription_id", "tenant_org_id", "tenant_name", "catalog_model_id", "status",
  "status_label", "requested_at", "started_on", "ended_on", "suspended_at"];
export const USAGE_FIELDS = ["tenant_org_id", "subscription_id", "catalog_model_id", "catalog_version_id",
  "account_count", "model_assets"];

const pick = (o, keys) => Object.fromEntries(keys.filter((k) => k in obj(o)).map((k) => [k, o[k]]));

export function subscriberRows(envelope) {
  const e = obj(envelope);
  const usage = listOf(e.usage).map((u) => pick(u, USAGE_FIELDS));
  return listOf(e.subscriptions).map((s) => {
    const row = pick(s, SUBSCRIBER_FIELDS);
    row.usage = usage.filter((u) => u.subscription_id === row.subscription_id);
    return row;
  });
}

/** Invoice lines as stated, with the separate revenue-line label the server names. */
export function invoiceLines(invoice) {
  return listOf(obj(invoice).lines).map((l) => ({ label: l.label, fee_bps: l.fee_bps, days: l.days,
    average_daily_assets: l.average_daily_assets, amount: l.amount }));
}
