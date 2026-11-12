"use client";

import { useEffect, useState } from "react";

import { requestJson } from "@/components/market/marketClient.mjs";
import { BUTTON, CARD, CARD_STYLE, ERROR_STYLE, EYEBROW, QUIET } from "@/components/market/marketStyles.mjs";
import {
  codeText,
  interpretSecuritySearch,
  searchSettings,
  securitySearchUrl,
} from "@/lib/market/catalogModel.mjs";
import { UNRESTRICTED_LICENSE_CLASS } from "@/lib/market/marketDefaults.mjs";
import {
  KIND_INDICATOR,
  KIND_SECURITY,
  categoryState,
  clearAll,
  indicatorBySeriesKey,
  indicatorGroups,
  isAllowed,
  isSelectableSecurity,
  isSelected,
  securityGroups,
  selectionLimit,
  toggle,
  toggleCategory,
} from "@/lib/market/selection.mjs";

/**
 * The left column: category chips (bulk), indicators by category, securities
 * by type. Every label, colour, provider name, license marker and limit comes
 * from the catalog; grouping, counting and toggling are lib/market/selection.mjs.
 *
 * dailyops1: the catalog lists only the SELECTABLE securities. The rest of the
 * security master is found with the search box (GET /api/market/securities/
 * search); a result with no price history is shown disabled with its reason.
 * `initialSearch` ({ text, state }) is for render tests only.
 */
export default function SelectionPanel({ catalog, selection, onChange, initialSearch }) {
  const [message, setMessage] = useState(null);
  const search = searchSettings(catalog);
  const [searchText, setSearchText] = useState(initialSearch?.text ?? "");
  // The answer is kept with the URL it answers; the state shown is derived, so
  // no state is set synchronously inside the effect.
  const [searchResult, setSearchResult] = useState(null);
  const searchUrl = securitySearchUrl(searchText, search);
  const searchState = initialSearch?.state
    ?? (!searchUrl ? { kind: "idle" }
      : searchResult?.url === searchUrl ? searchResult.state : { kind: "loading" });

  useEffect(() => {
    if (initialSearch || !searchUrl) return undefined;
    let alive = true;
    const timer = setTimeout(async () => {
      const next = interpretSecuritySearch(await requestJson(searchUrl));
      if (alive) setSearchResult({ url: searchUrl, state: next });
    }, 250);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [searchUrl, initialSearch]);
  const limit = selectionLimit(catalog);
  const groups = indicatorGroups(catalog);
  const secGroups = securityGroups(catalog);
  const bySeries = indicatorBySeriesKey(catalog);
  const licenseVocab = catalog.vocabularies?.license_classes ?? [];

  function apply(result) {
    setMessage(result.blocked ? result.message : null);
    if (!result.blocked) onChange(result.selection);
  }

  return (
    <aside className={`${CARD} min-w-0 self-start p-4`} style={CARD_STYLE} data-market-control="selection-panel">
      <div className="mb-3 flex items-baseline justify-between">
        <p className={EYEBROW}>Series</p>
        <div className="flex items-baseline gap-3">
          <span className={`${QUIET} tabular-nums`} data-market="selection-count">
            {selection.length} / {limit} selected
          </span>
          <button
            type="button"
            className={BUTTON}
            disabled={selection.length === 0}
            onClick={() => apply({ selection: clearAll(), blocked: false })}
          >
            Clear all
          </button>
        </div>
      </div>

      {message && (
        <p className="mb-3 text-xs text-[var(--2a-text-secondary)]" role="status" data-market="limit-message">
          {message}
        </p>
      )}

      <div className="mb-4 flex flex-wrap gap-1.5" data-market-control="category-chips">
        {groups.map((g) => {
          const state = categoryState(selection, g);
          return (
            <button
              key={g.key}
              type="button"
              data-chip-state={state}
              aria-pressed={state === "all" ? "true" : state === "some" ? "mixed" : "false"}
              onClick={() => apply(toggleCategory(selection, catalog, g))}
              className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs"
              style={{
                borderColor: g.color ?? "var(--2a-border)",
                background: state === "all" ? g.color ?? "var(--2a-navy)" : "#FFFFFF",
                color: state === "all" ? "#FFFFFF" : "var(--2a-text)",
              }}
            >
              <span
                aria-hidden="true"
                className="inline-block h-2 w-2 rounded-full border"
                style={{
                  borderColor: state === "all" ? "#FFFFFF" : g.color ?? "var(--2a-border)",
                  background: state === "none" ? "transparent" : state === "all" ? "#FFFFFF" : g.color,
                }}
              />
              {g.label}
            </button>
          );
        })}
      </div>

      <div className="max-h-[60vh] space-y-4 overflow-y-auto pr-1">
        {groups.map((g) => (
          <div key={g.key}>
            <p className={`${EYEBROW} mb-1`}>{g.label}</p>
            <ul className="space-y-0.5">
              {g.indicators.map((ind) => (
                <SeriesRow
                  key={ind.series_key}
                  checked={isSelected(selection, KIND_INDICATOR, ind.series_key)}
                  onToggle={() => apply(toggle(selection, catalog, KIND_INDICATOR, ind.series_key))}
                  color={ind.color}
                  name={ind.name}
                  provider={ind.source_provider}
                  license={
                    ind.license_class !== UNRESTRICTED_LICENSE_CLASS && licenseVocab.includes(ind.license_class)
                      ? codeText(ind.license_class)
                      : null
                  }
                />
              ))}
            </ul>
          </div>
        ))}

        {search && (
          <div data-market-control="security-search">
            <label className={`${EYEBROW} mb-1 block`} htmlFor="market-security-search">
              Find a security
            </label>
            <input
              id="market-security-search"
              type="search"
              value={searchText}
              maxLength={search.maxChars}
              onChange={(e) => setSearchText(e.target.value)}
              placeholder="Name, ticker or CUSIP"
              className="w-full rounded border border-[var(--2a-border)] bg-white px-2 py-1 text-sm text-[var(--2a-text)]"
            />
            {searchState.kind === "loading" && (
              <p className={`${QUIET} mt-1`} data-market="search-loading">
                Searching…
              </p>
            )}
            {searchState.kind === "error" && (
              <p className="mt-1 text-xs" style={{ color: ERROR_STYLE.color }} role="alert" data-market="search-error">
                {searchState.message}
              </p>
            )}
            {searchState.kind === "results" && (
              <div className="mt-1" data-market="search-results">
                {searchState.securities.length === 0 ? (
                  <p className={QUIET}>No security matches.</p>
                ) : (
                  <ul className="space-y-0.5">
                    {searchState.securities.map((sec) =>
                      isSelectableSecurity(sec) && isAllowed(catalog, KIND_SECURITY, sec.id) ? (
                        <SeriesRow
                          key={sec.id}
                          checked={isSelected(selection, KIND_SECURITY, sec.id)}
                          onToggle={() => apply(toggle(selection, catalog, KIND_SECURITY, sec.id))}
                          color={sec.color}
                          name={sec.ticker ? `${sec.name} (${sec.ticker})` : sec.name}
                          provider={bySeries.get(sec.series_key)?.source_provider}
                        />
                      ) : (
                        <UnselectableRow
                          key={sec.id}
                          name={sec.ticker ? `${sec.name} (${sec.ticker})` : sec.name}
                          reason={sec.unselectable_reason}
                        />
                      ),
                    )}
                  </ul>
                )}
              </div>
            )}
          </div>
        )}

        {secGroups.map((sg) => {
          const rows = (
            <ul className="space-y-0.5">
              {sg.securities.map((sec) => {
                const linked = bySeries.get(sec.series_key);
                return isSelectableSecurity(sec) ? (
                  <SeriesRow
                    key={sec.id}
                    checked={isSelected(selection, KIND_SECURITY, sec.id)}
                    onToggle={() => apply(toggle(selection, catalog, KIND_SECURITY, sec.id))}
                    color={sec.color}
                    name={sec.name}
                    provider={linked?.source_provider}
                    license={
                      linked &&
                      linked.license_class !== UNRESTRICTED_LICENSE_CLASS &&
                      licenseVocab.includes(linked.license_class)
                        ? codeText(linked.license_class)
                        : null
                    }
                  />
                ) : (
                  <UnselectableRow key={sec.id} name={sec.name} reason={sec.unselectable_reason} />
                );
              })}
            </ul>
          );
          const heading = `${codeText(sg.type)} · ${sg.securities.length}`;
          return sg.collapsed ? (
            <details key={sg.type} data-market="security-group-collapsed">
              <summary className={`${EYEBROW} cursor-pointer`}>{heading}</summary>
              <div className="mt-1">{rows}</div>
            </details>
          ) : (
            <div key={sg.type} data-market="security-group">
              <p className={`${EYEBROW} mb-1`}>{heading}</p>
              {rows}
            </div>
          );
        })}
      </div>
    </aside>
  );
}

function UnselectableRow({ name, reason }) {
  return (
    <li className="flex items-start gap-2 py-0.5 text-sm text-[var(--2a-text-muted)]" data-market="unselectable">
      <input type="checkbox" disabled className="mt-1" aria-label={name} />
      <span className="min-w-0">
        <span className="block truncate">{name}</span>
        <span className="block text-[11px]">{codeText(reason)}</span>
      </span>
    </li>
  );
}

function SeriesRow({ checked, onToggle, color, name, provider, license }) {
  return (
    <li>
      <label className="flex cursor-pointer items-center gap-2 py-0.5 text-sm text-[var(--2a-text)]">
        <input type="checkbox" checked={checked} onChange={onToggle} />
        <span aria-hidden="true" className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: color }} />
        <span className="min-w-0 flex-1 truncate">{name}</span>
        {provider && (
          <span
            className="shrink-0 rounded border border-[var(--2a-border)] px-1 text-[10px] uppercase tracking-wide text-[var(--2a-text-muted)]"
            data-market="provider-tag"
          >
            {provider}
          </span>
        )}
        {license && (
          <span className="shrink-0 text-[10px] italic text-[var(--2a-text-muted)]" data-market="license-marker">
            {license}
          </span>
        )}
      </label>
    </li>
  );
}
