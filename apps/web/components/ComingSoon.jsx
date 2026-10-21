// BUGFIX1 (DEAD-LINKS) — the page an unbuilt menu entry renders instead of a
// bare 404. The label comes from the ONE menu definition (labelFor — Rule 1);
// the entry itself is hidden from every menu by its `comingSoon` flag.
export default function ComingSoon({ label }) {
  return (
    <div className="mx-auto max-w-[720px] py-10" data-testid="coming-soon">
      <div
        className="bg-white px-8 py-10 text-center"
        style={{ border: "1px solid #ece8dd", borderRadius: 6 }}
      >
        <p className="text-[11px] font-bold uppercase tracking-[0.22em] text-[var(--2a-gold)]">
          Coming soon
        </p>
        <h1
          className="mt-2 text-2xl font-semibold text-[var(--2a-navy)]"
          style={{ fontFamily: "Spectral, Georgia, serif" }}
        >
          {label || "This page"}
        </h1>
        <p className="mx-auto mt-3 max-w-[52ch] text-[15px] text-[var(--2a-text-secondary)]">
          This part of the platform is being prepared and is not open yet. It will
          appear in your menu as soon as it is ready.
        </p>
        <a
          href="/dashboard"
          className="mt-6 inline-block text-sm font-semibold text-[var(--2a-navy)] underline underline-offset-4"
        >
          Return to the dashboard
        </a>
      </div>
    </div>
  );
}
