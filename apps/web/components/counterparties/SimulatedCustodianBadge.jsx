// CPSIM02 — the "Simulated custodian" badge on an account fed by a SIMULATED Altruist-compatible counterparty. Renders
// only when the server says so (envelope.custodian_feed.simulated === true — exactly true; a lost envelope shows nothing
// rather than guessing). Server-safe: no hooks.
export function isSimulatedFeed(envelope) {
  return envelope?.custodian_feed?.simulated === true;
}

export default function SimulatedCustodianBadge({ envelope }) {
  if (!isSimulatedFeed(envelope)) return null;
  return (
    <span data-testid="simulated-custodian-badge" title="This account's custodian feed comes from a simulated counterparty"
      className="ml-3 inline-block rounded border border-[#C5A880] bg-[#F5F1EB] px-2 py-0.5 align-middle text-xs font-semibold tracking-[0.08em] text-navy">
      SIMULATED CUSTODIAN</span>
  );
}
