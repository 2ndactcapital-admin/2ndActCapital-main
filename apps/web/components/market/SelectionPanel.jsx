"use client";

import { useState } from "react";

import { BUTTON, CARD, CARD_STYLE, EYEBROW, QUIET } from "@/components/market/marketStyles.mjs";
import { codeText } from "@/lib/market/catalogModel.mjs";
import { UNRESTRICTED_LICENSE_CLASS } from "@/lib/market/marketDefaults.mjs";
import {
  KIND_INDICATOR,
  KIND_SECURITY,
  categoryState,
  clearAll,
  indicatorBySeriesKey,
  indicatorGroups,
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
 */
export default function SelectionPanel({ catalog, selection, onChange }) {
  const [message, setMessage] = useState(null);
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
                  <li
                    key={sec.id}
                    className="flex items-start gap-2 py-0.5 text-sm text-[var(--2a-text-muted)]"
                    data-market="unselectable"
                  >
                    <input type="checkbox" disabled className="mt-1" aria-label={sec.name} />
                    <span className="min-w-0">
                      <span className="block truncate">{sec.name}</span>
                      <span className="block text-[11px]">{codeText(sec.unselectable_reason)}</span>
                    </span>
                  </li>
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
