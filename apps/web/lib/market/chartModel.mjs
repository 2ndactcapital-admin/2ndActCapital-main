/**
 * Everything the chart's SVG, notices and table need, from the selection, the
 * series response, the catalog and the settings (mkt04b). Pure: no React, no
 * DOM. Components only draw what this returns.
 *
 *   prepareChartData  parses the response ONCE per response (strings ->
 *                     floats in rebase.mjs) and precomputes, per series, the
 *                     as-of observation of every period.
 *   buildChartModel   re-measures every line from the anchor — cheap enough to
 *                     run on each animation frame while the anchor is dragged.
 *
 * Labels: the measure caption is the server's modes label; regime labels come
 * from key-dates vocabularies.regime_types; warnings and unavailable reasons
 * use vocabText (the server's [{key, label}] list when published, else the
 * code text). Series names, groups and colours are the catalog's.
 */

import { codeText, vocabText } from "./catalogModel.mjs";
import { MEASURE_INDEX, MEASURE_SIGMA, SCALE_LOG, TRANSFORM_LEVEL } from "./chartContract.mjs";
import { yearsAgo } from "./gridRequest.mjs";
import { FLOATING_NOTE } from "./gridView.mjs";
import { anchorFlagText, selectedBandRange } from "./keyDatesModel.mjs";
import { LABEL_GAP, layoutLabels } from "./labelLayout.mjs";
import { KIND_SECURITY } from "./marketDefaults.mjs";
import {
  STATUS_ABOVE,
  STATUS_BELOW,
  STATUS_FEW,
  STATUS_IN,
  bigMoves,
  cohortBand,
  direction,
  packFences,
  packStatus,
  quantile,
} from "./overlays.mjs";
import { asOfIndex, measureValue, parseSeries, rebaseSpec } from "./rebase.mjs";
import {
  AXIS_LINEAR_INDEX,
  AXIS_LOG_INDEX,
  AXIS_SIGMA,
  buildPeriods,
  formatNumber,
  isoToMs,
  linearTicks,
  logTicks,
  makeXScale,
  makeYScale,
  monthLabel,
  periodEnd,
  signed,
  timeTicks,
  toAxis,
  yDomain,
} from "./scales.mjs";
import { indicatorBySeriesKey, resolveSelection } from "./selection.mjs";

export const CHART_WIDTH = 960;
export const CHART_HEIGHT = 440;
export const MARGIN = { top: 18, right: 172, bottom: 30, left: 70 };
export const LINE_WIDTH = { base: 1, security: 2.2, outlier: 1.7, hovered: 2.8 };
export const FADED_OPACITY = 0.3;
export const DIMMED_OPACITY = 0.2;
export const FLOATING_DASH = "4 3";
export const FLAG_WIDTH = 118;
export const LABEL_MAX_CHARS = 24;

// Design tokens only; series colours are the catalog's.
export const REGIME_FILLS = ["#E8D5A3", "#E2E8F0"];
export const KEY_DATE_COLOR = "#C5A880";
// mkt04c: personal dates draw darker than the reference key dates; a selected
// range is shaded in a distinct light blue whatever the overlays.
export const MY_DATE_COLOR = "#334155";
export const SELECTED_BAND_FILL = "rgba(43,95,158,0.16)";
export const FLAG_MIN_WIDTH = 108;
export const FLAG_CHAR_WIDTH = 5.5;
export const FLAG_PADDING = 14;
export const PACK_COLOR = "#1B2B4B";

export const STATUS_TEXT = {
  [STATUS_ABOVE]: "Outlier above pack",
  [STATUS_BELOW]: "Outlier below pack",
  [STATUS_IN]: "In pack",
  [STATUS_FEW]: "Too few series",
};
export const ARROWS = { up: "↑", down: "↓", flat: "→" };
export const EM_DASH = "—";
export const LEGEND_TEXT = {
  band: "Middle half of the series (25th to 75th percentile)",
  median: "Median",
  keyDates: "Key dates",
  myDates: "My date",
  selectedPeriod: "Selected period",
  outlier: "Outside the pack",
  bigMove: "Unusually large move",
};

const round1 = (v) => Math.round(v * 10) / 10;

// ── Preparation (once per response) ─────────────────────────────────────────

/**
 * Lines in selection order, one per distinct series_key present in the
 * response: {key, label, color, isSecurity, group, defaultTransform, units,
 * series, obsIdx[], lastPeriod}. `periods` are the chart's x positions.
 */
export function prepareChartData(response, selection, catalog, resolution) {
  const apiByKey = new Map((Array.isArray(response?.series) ? response.series : []).map((s) => [s.series_key, s]));
  const indicators = indicatorBySeriesKey(catalog);
  const entries = [];
  for (const r of resolveSelection(selection, catalog)) {
    if (entries.some((e) => e.key === r.series_key) || !apiByKey.has(r.series_key)) continue;
    const ind = indicators.get(r.series_key);
    const isSecurity = r.kind === KIND_SECURITY;
    const sec = isSecurity ? (catalog.securities ?? []).find((s) => s.id === r.key) : null;
    entries.push({
      key: r.series_key,
      label: r.label,
      color: r.color,
      isSecurity,
      group: sec ? codeText(sec.security_type) : ind?.category ?? "",
      defaultTransform: ind?.default_transform ?? null,
      units: ind?.units ?? null,
      series: parseSeries(apiByKey.get(r.series_key)),
    });
  }
  const frequency = response?.requested_frequency ?? resolution;
  const periods = buildPeriods(entries.map((e) => e.series), frequency);
  const periodDates = periods.map((p) => p.date);
  const maxDate = periodDates[periodDates.length - 1] ?? null;
  for (const e of entries) {
    const { dates } = e.series;
    e.obsIdx = periodDates.map((d) => asOfIndex(dates, d));
    if (dates.length === 0) {
      e.lastPeriod = -1;
    } else {
      const end = periodEnd(dates[dates.length - 1], frequency);
      e.lastPeriod = asOfIndex(periodDates, end > maxDate ? maxDate : end);
    }
  }
  return { entries, periods, frequency };
}

/** The period the anchor snaps to: the last period on or before it, else 0. */
export function snapAnchorIndex(periods, iso) {
  if (periods.length === 0) return -1;
  return Math.max(asOfIndex(periods.map((p) => p.date), iso ?? ""), 0);
}

// ── Model (per anchor / setting / size / hover) ─────────────────────────────

function pathFor(xs, ys) {
  let d = "";
  let pen = false;
  for (let i = 0; i < xs.length; i += 1) {
    const y = ys[i];
    if (y === null || !Number.isFinite(y)) {
      pen = false;
      continue;
    }
    d += `${pen ? "L" : "M"}${round1(xs[i])},${round1(y)}`;
    pen = true;
  }
  return d;
}

function bandPath(xs, band, yOf) {
  let d = "";
  let i = 0;
  while (i < band.length) {
    if (band[i] === null) {
      i += 1;
      continue;
    }
    let j = i;
    while (j + 1 < band.length && band[j + 1] !== null) j += 1;
    const upper = [];
    const lower = [];
    for (let k = i; k <= j; k += 1) {
      upper.push(`${round1(xs[k])},${round1(yOf(band[k].q75))}`);
      lower.unshift(`${round1(xs[k])},${round1(yOf(band[k].q25))}`);
    }
    d += `M${upper.join("L")}L${lower.join("L")}Z`;
    i = j + 1;
  }
  return d;
}

function tickDecimals(values) {
  let n = 0;
  for (const v of values) {
    const frac = String(v).split(".")[1];
    if (frac) n = Math.max(n, Math.min(frac.length, 4));
  }
  return n;
}

function measuredText(measure, v) {
  if (measure === MEASURE_INDEX) return `Index ${formatNumber(v, 1)} · ${signed(v - 100, 1)}% vs anchor`;
  return `${signed(v, 2)}σ vs anchor`;
}

function groupNotices(map, textOf) {
  return [...map.entries()].map(([code, names]) => ({ key: code, text: `${textOf(code)}: ${names.join(", ")}` }));
}

/**
 * settings: {anchor, measure, scale, overlays: {events, band, emphasis}}
 * ctx: {modes (catalog vocabularies.modes), seriesVocab (series response
 *       vocabularies), keyDates (interpretKeyDates state or null), width,
 *       height, hoverKey, dateSelection (mkt04c: keyDatesModel
 *       resolveDateSelection, or null)}
 */
export function buildChartModel(prepared, settings, ctx = {}) {
  const width = ctx.width ?? CHART_WIDTH;
  const height = ctx.height ?? CHART_HEIGHT;
  const plot = {
    left: MARGIN.left,
    top: MARGIN.top,
    right: width - MARGIN.right,
    bottom: height - MARGIN.bottom,
  };
  plot.width = plot.right - plot.left;
  plot.height = plot.bottom - plot.top;
  const measure = settings.measure;
  const log = measure === MEASURE_INDEX && settings.scale === SCALE_LOG;
  const overlays = settings.overlays ?? {};
  const seriesVocab = ctx.seriesVocab ?? {};
  const { entries, periods } = prepared;

  const model = {
    width,
    height,
    plot,
    measure,
    log,
    empty: periods.length === 0 || (measure !== MEASURE_INDEX && measure !== MEASURE_SIGMA),
    periods,
    periodXs: [],
    anchor: null,
    lines: [],
    hitLines: [],
    yTicks: [],
    xTicks: [],
    axisTitle: "",
    regimes: [],
    keyDateLines: [],
    myDateLines: [],
    selectedBand: null,
    band: null,
    dots: [],
    labels: [],
    notices: [],
    table: [],
    legend: [],
    byKey: new Map(),
    baselineY: null,
  };
  if (model.empty) return model;

  // Anchor: snapped to a period; every line is measured from that date.
  const anchorIndex = snapAnchorIndex(periods, settings.anchor);
  const anchorDate = periods[anchorIndex].date;

  const measured = [];
  const unavailable = new Map();
  const warnings = new Map();
  const floating = [];
  const add = (map, code, name) => map.set(code, [...(map.get(code) ?? []), name]);
  for (const e of entries) {
    const spec = rebaseSpec(e.series, { measure, anchor: anchorDate, defaultTransform: e.defaultTransform });
    if (spec.unavailable !== null) {
      add(unavailable, spec.unavailable, e.label);
      continue;
    }
    for (const w of spec.warnings) add(warnings, w, e.label);
    if (spec.floating) floating.push(e.label);
    const values = e.obsIdx.map((oi, p) => (oi >= 0 && p <= e.lastPeriod ? measureValue(spec, e.series.values[oi]) : null));
    measured.push({ e, spec, values, axis: values.map((v) => toAxis(v, log)) });
  }

  // Scales.
  const kind = measure === MEASURE_SIGMA ? AXIS_SIGMA : log ? AXIS_LOG_INDEX : AXIS_LINEAR_INDEX;
  const domain = yDomain(measured.flatMap((m) => m.values), kind);
  const y = makeYScale(domain, log, plot.top, plot.bottom);
  const t0 = periods[0].ms;
  const t1 = periods.length > 1 ? periods[periods.length - 1].ms : t0 + 86400000;
  const x = makeXScale(t0, t1, plot.left, plot.right);
  const periodXs = periods.map((p) => x.toPx(p.ms));
  model.periodXs = periodXs;
  model.domain = domain;
  const axisSpan = toAxis(domain[1], log) - toAxis(domain[0], log);

  // The pack at the latest date, and twelve months before it.
  const last = periods.length - 1;
  const idx12 = asOfIndex(periods.map((p) => p.date), yearsAgo(periods[last].date, 1));
  const fences = packFences(measured.map((m) => m.axis[last]), axisSpan);
  const emphasis = overlays.emphasis === true && fences !== null;

  const hoverKey = measured.some((m) => m.e.key === ctx.hoverKey) ? ctx.hoverKey : null;
  const lines = measured.map((m) => {
    const status = m.axis[last] === null ? null : packStatus(m.axis[last], fences);
    const outlier = status === STATUS_ABOVE || status === STATUS_BELOW;
    let lineWidth = m.e.isSecurity ? LINE_WIDTH.security : LINE_WIDTH.base;
    let opacity = 1;
    if (emphasis && outlier) lineWidth = Math.max(lineWidth, LINE_WIDTH.outlier);
    if (emphasis && status === STATUS_IN) opacity = FADED_OPACITY;
    if (hoverKey !== null) {
      if (m.e.key === hoverKey) {
        lineWidth = LINE_WIDTH.hovered;
        opacity = 1;
      } else {
        opacity = Math.min(opacity, DIMMED_OPACITY);
      }
    }
    const ys = m.values.map((v) => (v === null ? null : y.toPx(v)));
    model.byKey.set(m.e.key, { ...m, ys, status });
    return {
      key: m.e.key,
      label: m.e.label,
      color: m.e.color,
      width: lineWidth,
      opacity,
      dash: m.spec.floating ? FLOATING_DASH : null,
      floating: m.spec.floating,
      status,
      outlier,
      d: pathFor(periodXs, ys),
      ys,
    };
  });
  // The hovered line is drawn last, on top of the others.
  lines.sort((a, b) => (a.key === hoverKey) - (b.key === hoverKey));
  model.lines = lines;
  model.hitLines = lines.map((l) => ({ key: l.key, ys: l.ys }));
  model.hoverKey = hoverKey;

  // Axes.
  const tickValues = log ? logTicks(domain[0], domain[1]) : linearTicks(domain[0], domain[1]);
  const dec = tickDecimals(tickValues);
  model.yTicks = tickValues.map((v) => ({
    value: v,
    y: y.toPx(v),
    label: measure === MEASURE_SIGMA ? `${signed(v, dec)}σ` : formatNumber(v, dec),
  }));
  const base = measure === MEASURE_INDEX ? 100 : 0;
  model.baselineY = base >= domain[0] && base <= domain[1] ? y.toPx(base) : null;
  model.xTicks = timeTicks(t0, t1).map((t) => ({ x: x.toPx(t.ms), label: t.label }));
  const scaleText = measure === MEASURE_INDEX ? (log ? ", log scale" : ", linear scale") : "";
  model.axisTitle = `${vocabText(ctx.modes, measure)} (${monthLabel(anchorDate)}${scaleText})`;

  const ax = periodXs[anchorIndex];
  const flag = anchorFlagText(anchorDate, anchorIndex, periods, ctx.dateSelection ?? null);
  const flagWidth = Math.max(FLAG_MIN_WIDTH, Math.round(flag.length * FLAG_CHAR_WIDTH + FLAG_PADDING));
  model.anchor = {
    index: anchorIndex,
    date: anchorDate,
    x: ax,
    flag,
    flagWidth,
    flagSide: ax + flagWidth + FLAG_WIDTH - FLAG_MIN_WIDTH > plot.right ? "left" : "right",
  };

  // The picked range (mkt04c): shaded whether or not the events overlay is on.
  const picked = selectedBandRange(ctx.dateSelection ?? null);
  if (picked) {
    const s = isoToMs(picked.start);
    const f = isoToMs(picked.end);
    if (s !== null && f !== null && f >= t0 && s <= t1) {
      model.selectedBand = { x0: x.toPx(Math.max(s, t0)), x1: x.toPx(Math.min(f, t1)) };
    }
  }

  // Cohort band and median.
  if (overlays.band === true) {
    const band = cohortBand(measured.map((m) => m.axis));
    const yAxis = (a) => y.toPx(log ? 10 ** a : a);
    if (band.some((b) => b !== null)) {
      model.band = {
        d: bandPath(periodXs, band, yAxis),
        median: pathFor(periodXs, band.map((b) => (b === null ? null : yAxis(b.q50)))),
      };
    }
  }

  // Big-move dots (with outlier highlighting).
  if (overlays.emphasis === true) {
    for (const l of lines) {
      const m = model.byKey.get(l.key);
      for (const mv of bigMoves(m.axis)) {
        if (l.ys[mv.index] === null) continue;
        model.dots.push({ key: `${l.key}:${mv.index}`, series: l.key, x: periodXs[mv.index], y: l.ys[mv.index], color: l.color });
      }
    }
  }

  // Regimes and key dates.
  const kd = ctx.keyDates;
  if (overlays.events === true && kd?.kind === "ready") {
    const types = (kd.vocabularies?.regime_types ?? []).map((t) => t?.key);
    for (const r of kd.regimes) {
      const s = isoToMs(r.start_date);
      const f = isoToMs(r.end_date);
      if (s === null || f === null || f < t0 || s > t1) continue;
      const ti = types.indexOf(r.regime_type);
      model.regimes.push({
        key: `${r.regime_type}:${r.start_date}`,
        x0: x.toPx(Math.max(s, t0)),
        x1: x.toPx(Math.min(f, t1)),
        label: r.name,
        fill: REGIME_FILLS[(ti < 0 ? 0 : ti) % REGIME_FILLS.length],
      });
    }
    for (const k of kd.keyDates) {
      const s = isoToMs(k.start_date);
      if (s === null || s < t0 || s > t1) continue;
      model.keyDateLines.push({ key: k.slug, x: x.toPx(s), label: k.name });
    }
    for (const c of kd.customDates ?? []) {
      const s = isoToMs(c?.event_date);
      if (s === null || s < t0 || s > t1) continue;
      model.myDateLines.push({ key: c.id, x: x.toPx(s), label: c.name });
    }
  }

  // End-of-line labels.
  const wanted = [];
  for (const l of lines) {
    let i = l.ys.length - 1;
    while (i >= 0 && l.ys[i] === null) i -= 1;
    if (i < 0) continue;
    const text = l.label.length > LABEL_MAX_CHARS ? `${l.label.slice(0, LABEL_MAX_CHARS - 1)}…` : l.label;
    wanted.push({ key: l.key, y: Math.min(Math.max(l.ys[i], plot.top), plot.bottom), text, color: l.color });
  }
  model.labels = layoutLabels(wanted, { top: plot.top + 4, bottom: plot.bottom - 4, gap: LABEL_GAP });

  // Notices.
  model.notices = [
    ...groupNotices(unavailable, (c) => vocabText(seriesVocab.unavailable_reasons, c)),
    ...groupNotices(warnings, (c) => vocabText(seriesVocab.warnings, c)),
    ...(floating.length ? [{ key: "floating", text: `${floating.join(", ")}: ${FLOATING_NOTE}` }] : []),
  ];

  // Trends and outliers.
  const latestAxis = measured.map((m) => m.axis[last]).filter((a) => a !== null).sort((a, b) => a - b);
  const median = fences?.q50 ?? quantile(latestAxis, 0.5);
  const unit = measure === MEASURE_INDEX ? "%" : "σ";
  const dp = measure === MEASURE_INDEX ? 1 : 2;
  model.table = lines
    .map((l) => {
      const m = model.byKey.get(l.key);
      const latest = m.values[last];
      const earlier = idx12 >= 0 ? m.values[idx12] : null;
      const since = latest === null ? null : measure === MEASURE_INDEX ? latest - 100 : latest;
      let twelve = null;
      if (latest !== null && earlier !== null) {
        if (measure === MEASURE_SIGMA) twelve = latest - earlier;
        else if (earlier > 0) twelve = 100 * (latest / earlier - 1);
      }
      const dir = direction(m.axis[last], idx12 >= 0 ? m.axis[idx12] : null);
      return {
        key: l.key,
        label: l.label,
        color: l.color,
        group: m.e.group,
        since: since === null ? EM_DASH : `${signed(since, dp)}${unit}`,
        twelve: twelve === null || dir === null ? EM_DASH : `${ARROWS[dir.direction]} ${signed(twelve, dp)}${unit}`,
        direction: dir?.direction ?? null,
        status: l.status,
        statusText: l.status === null ? EM_DASH : STATUS_TEXT[l.status],
        distance: m.axis[last] === null || median === null ? -1 : Math.abs(m.axis[last] - median),
      };
    })
    .sort((a, b) => b.distance - a.distance || a.label.localeCompare(b.label));

  // Legend for the overlays that are on.
  if (model.band) {
    model.legend.push({ key: "band", kind: "band", label: LEGEND_TEXT.band, color: PACK_COLOR });
    model.legend.push({ key: "median", kind: "median", label: LEGEND_TEXT.median, color: PACK_COLOR });
  }
  if (overlays.events === true && kd?.kind === "ready") {
    (kd.vocabularies?.regime_types ?? []).forEach((t, i) => {
      if (t?.key && typeof t.label === "string") {
        model.legend.push({ key: `regime:${t.key}`, kind: "regime", label: t.label, color: REGIME_FILLS[i % REGIME_FILLS.length] });
      }
    });
    model.legend.push({ key: "keyDates", kind: "keyDate", label: LEGEND_TEXT.keyDates, color: KEY_DATE_COLOR });
    if (model.myDateLines.length > 0) {
      model.legend.push({ key: "myDates", kind: "myDate", label: LEGEND_TEXT.myDates, color: MY_DATE_COLOR });
    }
  }
  if (model.selectedBand) {
    model.legend.push({ key: "selectedPeriod", kind: "selectedPeriod", label: LEGEND_TEXT.selectedPeriod, color: SELECTED_BAND_FILL });
  }
  if (emphasis) model.legend.push({ key: "outlier", kind: "outlier", label: LEGEND_TEXT.outlier, color: PACK_COLOR });
  if (overlays.emphasis === true) model.legend.push({ key: "bigMove", kind: "bigMove", label: LEGEND_TEXT.bigMove, color: PACK_COLOR });

  return model;
}

/**
 * The hover card for a hitTest result: {key, name, date, valueText, rawText}.
 * rawText (the server's own string plus the catalog's units) only for series
 * whose default_transform is level.
 */
export function hoverCard(model, hit) {
  const m = hit ? model.byKey.get(hit.key) : null;
  if (!m) return null;
  const v = m.values[hit.index];
  const oi = m.e.obsIdx[hit.index];
  if (v === null || oi < 0) return null;
  const units = m.e.units ? ` ${m.e.units}` : "";
  return {
    key: hit.key,
    name: m.e.label,
    color: m.e.color,
    date: m.e.series.dates[oi],
    valueText: measuredText(model.measure, v),
    rawText: m.e.defaultTransform === TRANSFORM_LEVEL ? `${m.e.series.raw[oi]}${units}` : null,
    x: hit.x,
    y: hit.y,
  };
}
