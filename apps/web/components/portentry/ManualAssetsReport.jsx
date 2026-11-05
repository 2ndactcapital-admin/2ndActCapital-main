// BUGFIX3 (MUSE-OLIVO-M, Northline) — the org's manual assets and how many current positions (and owners) use each,
// so an org admin can SEE whether several held-away lines share one asset or made several. A name carried by more
// than one manual asset is flagged as a possible duplicate; nothing is merged from here.
import { manualAssetNote } from "@/lib/bugfix3.mjs";

export default function ManualAssetsReport({ envelope }) {
  const rows = Array.isArray(envelope?.rows?.assets) ? envelope.rows.assets : null;
  if (!rows) return null;
  return (
    <section className="mt-8 rounded-[6px] border border-[#ece8dd] bg-white p-6 text-sm">
      <h2 className="font-[Spectral,Georgia,serif] text-xl text-[#1B2B4B]">Manual assets</h2>
      <p className="mt-1 text-[#64748B]">Holdings not in the security master, kept for this organisation.</p>
      {rows.length === 0 ? <p className="mt-3 text-[#64748B]">None.</p> : (
        <ul className="mt-3 divide-y divide-[#E2E8F0]">
          {rows.map((a) => (
            <li key={a.id} className="py-2">
              <span className="text-[#0F172A]">{a.name}</span>
              <span className="text-[#64748B]"> · {manualAssetNote(a)}</span>
              {a.same_name_count > 1 && (
                <span className="ml-2 text-xs text-[#9B2335]">{a.same_name_count} manual assets share this name</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
