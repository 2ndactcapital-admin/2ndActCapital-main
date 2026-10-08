/**
 * The grid response as a display model (mkt04a Task 5c-5d). Pure: no React, no
 * DOM.
 *
 * The client never re-derives a value. Cells are the server's decimal strings,
 * shown as returned; the only change is display-only digit grouping of the
 * integer part, done on the TEXT — no Number(), parseFloat() or parseInt()
 * ever touches a value, so "0.123456" stays "0.123456" and a 20-digit value
 * keeps every digit.
 */

import { vocabText } from "./catalogModel.mjs";
import { resolveSelection } from "./selection.mjs";

export const EM_DASH = "—";
export const FLOATING_NOTE = "starts after the anchor";

const DECIMAL_TEXT = /^(-?)(\d+)(\.\d+)?$/;

/** A server cell as display text. null -> em dash; non-decimal text verbatim. */
export function formatCell(value) {
  if (value === null || value === undefined) return EM_DASH;
  const text = String(value);
  const m = DECIMAL_TEXT.exec(text);
  if (!m) return text;
  const [, sign, intPart, frac = ""] = m;
  return `${sign}${intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}${frac}`;
}

/**
 * response: the POST /market/grid body. selection + catalog: what the user
 * picked (for the header's name and colour). Returns
 *   { columns: [{key, label, color, floating, notes, unavailable}],
 *     rows: [{date, cells: [{kind: "value"|"unavailable"|"skip", text, rowSpan?}]}] }
 * Columns follow the selection order (one per distinct series); rows follow
 * the server's order (newest first), untouched.
 */
export function buildGridView(response, selection, catalog) {
  const vocab = response?.vocabularies ?? {};
  const meta = new Map((response?.series ?? []).map((s) => [s.series_key, s]));
  const rows = Array.isArray(response?.rows) ? response.rows : [];

  const columns = [];
  for (const r of resolveSelection(selection, catalog)) {
    if (columns.some((c) => c.key === r.series_key)) continue;
    const m = meta.get(r.series_key) ?? {};
    const notes = [];
    if (m.floating === true) notes.push(FLOATING_NOTE);
    for (const w of Array.isArray(m.warnings) ? m.warnings : []) notes.push(vocabText(vocab.warnings, w));
    columns.push({
      key: r.series_key,
      label: r.label,
      color: r.color,
      floating: m.floating === true,
      notes,
      unavailable: m.unavailable_reason ? vocabText(vocab.unavailable_reasons, m.unavailable_reason) : null,
    });
  }

  const outRows = rows.map((row, i) => ({
    date: row.date,
    cells: columns.map((c) => {
      if (c.unavailable) {
        return i === 0 ? { kind: "unavailable", text: c.unavailable, rowSpan: rows.length } : { kind: "skip" };
      }
      return { kind: "value", text: formatCell(row.cells?.[c.key] ?? null) };
    }),
  }));

  return { columns, rows: outRows };
}
