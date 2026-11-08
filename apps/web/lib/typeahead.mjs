// CATCHALL1 (HollisMuse Low) — a record picker searches on ANY change of its text (typed, pasted, browser autofill or
// a programmatic fill), never only on a key event or a button: the search is driven by the VALUE (an effect on the
// query), debounced, and an older response never overwrites a newer one. EntityPicker (portentry2) is the model;
// components/ui/useTypeahead.js is the one hook the other pickers share.

export const TYPEAHEAD_DELAY_MS = 300;

/** Whether ``query`` should be searched now (long enough, and not the one already searched). */
export function shouldSearch(query, lastSearched, minChars = 1) {
  const q = String(query ?? "").trim();
  return q.length >= minChars && q !== lastSearched;
}

/** A sequence guard: next() stamps a request; isLatest(n) says whether its response may be applied. */
export function sequence() {
  let n = 0;
  return { next: () => ++n, isLatest: (k) => k === n };
}
