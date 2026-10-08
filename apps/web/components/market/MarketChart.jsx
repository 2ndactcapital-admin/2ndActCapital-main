"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";

import AnchorSlider from "@/components/market/AnchorSlider";
import TrendsTable from "@/components/market/TrendsTable";
import { CARD, CARD_STYLE, QUIET } from "@/components/market/marketStyles.mjs";
import { CHART_HEIGHT, CHART_WIDTH, KEY_DATE_COLOR, buildChartModel, hoverCard } from "@/lib/market/chartModel.mjs";
import { createFrameScheduler } from "@/lib/market/chartRequest.mjs";
import { hitTest, nearestIndex, toLogical } from "@/lib/market/hitTest.mjs";

const NAVY = "#1B2B4B";
const MUTED = "#64748B";
const SECONDARY = "#334155";
const GRID = "#E2E8F0";
const MIN_WIDTH = 520;

/**
 * The SVG chart (mkt04b Task 4b-4f): lines, axes, overlays, the draggable
 * anchor bar with its range input, hover, legend, notices and the trends
 * table. Everything drawn comes from lib/market/chartModel.mjs; this component
 * only sizes the drawing (ResizeObserver), turns pointer and keyboard input
 * into an anchor date or a hover target, and coalesces both onto animation
 * frames so a drag re-measures lines once per frame, not per pointer event.
 *
 * Props: prepared (prepareChartData), settings, onAnchorChange(iso), modes,
 * seriesVocab, keyDates (interpretKeyDates state), initialWidth (tests).
 */
export default function MarketChart({ prepared, settings, onAnchorChange, modes, seriesVocab, keyDates, initialWidth }) {
  const boxRef = useRef(null);
  const svgRef = useRef(null);
  const geomRef = useRef(null);
  const [width, setWidth] = useState(initialWidth ?? CHART_WIDTH);
  const [hover, setHover] = useState(null);
  const [dragging, setDragging] = useState(false);
  const clipId = `market-plot-${useId().replace(/[^A-Za-z0-9_-]/g, "")}`;

  const model = useMemo(
    () =>
      buildChartModel(prepared, settings, {
        modes,
        seriesVocab,
        keyDates,
        width,
        height: CHART_HEIGHT,
        hoverKey: hover?.key ?? null,
      }),
    [prepared, settings, modes, seriesVocab, keyDates, width, hover],
  );

  // Pointer callbacks run outside render; they read the latest geometry here.
  useEffect(() => {
    geomRef.current = {
      periods: model.periods,
      periodXs: model.periodXs,
      hitLines: model.hitLines,
      width: model.width,
      height: model.height,
      onAnchorChange,
    };
  });

  useEffect(() => {
    const el = boxRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver((entries) => {
      const w = Math.round(entries[0]?.contentRect?.width ?? 0);
      if (w > 0) setWidth(Math.max(w, MIN_WIDTH));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Drag and hover are coalesced onto animation frames; created on the client only.
  const framesRef = useRef(null);
  useEffect(() => {
    const geometry = (clientX, clientY) => {
      const g = geomRef.current;
      const svg = svgRef.current;
      if (!g || !svg) return null;
      return { g, p: toLogical(clientX, clientY, svg.getBoundingClientRect(), g.width, g.height) };
    };
    const drag = createFrameScheduler((clientX) => {
      const at = geometry(clientX, 0);
      const i = at ? nearestIndex(at.g.periodXs, at.p.x) : -1;
      if (i >= 0) at.g.onAnchorChange(at.g.periods[i].date);
    });
    const hover = createFrameScheduler(({ clientX, clientY }) => {
      const at = geometry(clientX, clientY);
      if (at) setHover(hitTest(at.p, at.g.periodXs, at.g.hitLines));
    });
    framesRef.current = { drag, hover };
    return () => {
      drag.cancel();
      hover.cancel();
      framesRef.current = null;
    };
  }, []);

  if (model.empty) {
    return (
      <p className={`${QUIET} py-10 text-center`} data-market="chart-empty">
        No observations to chart for this selection.
      </p>
    );
  }

  const { plot, anchor, periods } = model;
  const card = hoverCard(model, hover);
  const startDrag = (e) => {
    e.currentTarget.setPointerCapture?.(e.pointerId);
    setDragging(true);
    setHover(null);
    framesRef.current?.drag.schedule(e.clientX);
  };
  const moveDrag = (e) => {
    if (dragging) framesRef.current?.drag.schedule(e.clientX);
    else framesRef.current?.hover.schedule({ clientX: e.clientX, clientY: e.clientY });
  };
  const endDrag = (e) => {
    e.currentTarget.releasePointerCapture?.(e.pointerId);
    setDragging(false);
  };

  return (
    <div className="min-w-0 space-y-3">
      <div className={`${CARD} relative min-w-0 p-2`} style={CARD_STYLE} ref={boxRef} data-market="chart">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${model.width} ${model.height}`}
          width="100%"
          height={model.height}
          role="img"
          aria-label={model.axisTitle}
          data-chart="svg"
          style={{ display: "block", touchAction: "none", userSelect: "none" }}
        >
          <defs>
            <clipPath id={clipId}>
              <rect x={plot.left} y={plot.top} width={plot.width} height={plot.height} />
            </clipPath>
          </defs>

          {model.regimes.map((r) => (
            <rect
              key={r.key}
              x={r.x0}
              y={plot.top}
              width={Math.max(r.x1 - r.x0, 1)}
              height={plot.height}
              fill={r.fill}
              fillOpacity={0.45}
              data-chart="regime-band"
            >
              <title>{r.label}</title>
            </rect>
          ))}

          {model.yTicks.map((t) => (
            <g key={t.value}>
              <line x1={plot.left} x2={plot.right} y1={t.y} y2={t.y} stroke={GRID} strokeWidth={1} />
              <text x={plot.left - 8} y={t.y} dy="0.32em" textAnchor="end" fontSize={11} fill={MUTED} className="tabular-nums">
                {t.label}
              </text>
            </g>
          ))}
          {model.baselineY !== null && (
            <line x1={plot.left} x2={plot.right} y1={model.baselineY} y2={model.baselineY} stroke={MUTED} strokeWidth={1} data-chart="baseline" />
          )}
          {model.xTicks.map((t) => (
            <g key={t.label}>
              <line x1={t.x} x2={t.x} y1={plot.bottom} y2={plot.bottom + 4} stroke={MUTED} />
              <text x={t.x} y={plot.bottom + 17} textAnchor="middle" fontSize={11} fill={MUTED}>
                {t.label}
              </text>
            </g>
          ))}
          <line x1={plot.left} x2={plot.right} y1={plot.bottom} y2={plot.bottom} stroke={MUTED} strokeWidth={1} />
          <text
            transform={`translate(16 ${plot.top + plot.height / 2}) rotate(-90)`}
            textAnchor="middle"
            fontSize={11}
            fill={SECONDARY}
            data-chart="y-title"
          >
            {model.axisTitle}
          </text>

          <g clipPath={`url(#${clipId})`}>
            {model.band && (
              <g data-chart="cohort-band">
                <path d={model.band.d} fill={NAVY} fillOpacity={0.07} stroke="none" />
                <path d={model.band.median} fill="none" stroke={NAVY} strokeOpacity={0.55} strokeWidth={1} strokeDasharray="5 3" data-chart="median" />
              </g>
            )}
            {model.keyDateLines.map((k) => (
              <line
                key={k.key}
                x1={k.x}
                x2={k.x}
                y1={plot.top}
                y2={plot.bottom}
                stroke={KEY_DATE_COLOR}
                strokeWidth={1}
                strokeDasharray="2 3"
                data-chart="key-date"
              >
                <title>{k.label}</title>
              </line>
            ))}
            {model.lines.map((l) => (
              <path
                key={l.key}
                d={l.d}
                fill="none"
                stroke={l.color}
                strokeWidth={l.width}
                strokeOpacity={l.opacity}
                strokeDasharray={l.dash ?? undefined}
                strokeLinejoin="round"
                vectorEffect="non-scaling-stroke"
                data-chart-line={l.key}
                data-floating={l.floating ? "true" : "false"}
              />
            ))}
            {model.dots.map((d) => (
              <circle key={d.key} cx={d.x} cy={d.y} r={3} fill="#FFFFFF" stroke={d.color} strokeWidth={1.5} data-chart="big-move" />
            ))}
          </g>

          {model.labels.map((l) => (
            <text key={l.key} x={plot.right + 8} y={l.y} dy="0.32em" fontSize={11} fill={l.color} data-chart="end-label">
              {l.text}
            </text>
          ))}

          {card && (
            <g pointerEvents="none" data-chart="hover">
              <line x1={card.x} x2={card.x} y1={plot.top} y2={plot.bottom} stroke={MUTED} strokeWidth={0.75} />
              <circle cx={card.x} cy={card.y} r={4} fill={card.color} stroke="#FFFFFF" strokeWidth={1.5} />
            </g>
          )}

          <rect
            x={plot.left}
            y={plot.top}
            width={plot.width}
            height={plot.height}
            fill="transparent"
            onPointerDown={startDrag}
            onPointerMove={moveDrag}
            onPointerUp={endDrag}
            onPointerLeave={() => {
              framesRef.current?.hover.cancel();
              setHover(null);
            }}
            data-chart="hit-area"
          />

          <g data-chart="anchor-bar" style={{ cursor: "ew-resize" }} onPointerDown={startDrag} onPointerMove={moveDrag} onPointerUp={endDrag}>
            <line x1={anchor.x} x2={anchor.x} y1={plot.top} y2={plot.bottom} stroke={NAVY} strokeWidth={1.5} />
            <rect x={anchor.x - 6} y={plot.top} width={12} height={plot.height} fill="transparent" />
            <g transform={`translate(${anchor.flagSide === "left" ? anchor.x - 112 : anchor.x + 4} ${plot.top + 2})`}>
              <rect width={108} height={18} rx={3} fill={NAVY} />
              <text x={54} y={12.5} textAnchor="middle" fontSize={10.5} fill="#FFFFFF" data-chart="anchor-flag">
                {anchor.flag}
              </text>
            </g>
          </g>
        </svg>

        {card && (
          <div
            className="pointer-events-none absolute z-10 rounded-[6px] border bg-white px-2.5 py-1.5 text-xs"
            style={{
              ...CARD_STYLE,
              left: `${(card.x / model.width) * 100}%`,
              top: `${card.y + 12}px`,
              transform: card.x > model.width * 0.65 ? "translateX(calc(-100% - 12px))" : "translateX(12px)",
            }}
            data-chart="hover-card"
          >
            <p className="flex items-center gap-1.5 font-semibold text-[var(--2a-navy)]">
              <span aria-hidden="true" className="inline-block h-2 w-2 rounded-sm" style={{ background: card.color }} />
              {card.name}
            </p>
            <p className="text-[var(--2a-text-muted)]">{card.date}</p>
            <p className="tabular-nums text-[var(--2a-text)]">{card.valueText}</p>
            {card.rawText && <p className="tabular-nums text-[var(--2a-text-secondary)]">{card.rawText}</p>}
          </div>
        )}

        <div className="px-2 pb-1 pt-2" style={{ paddingLeft: plot.left, paddingRight: model.width - plot.right }}>
          <AnchorSlider periods={periods} index={anchor.index} valueText={anchor.flag} onAnchorChange={onAnchorChange} />
        </div>
      </div>

      {model.legend.length > 0 && (
        <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--2a-text-secondary)]" data-chart="legend">
          {model.legend.map((item) => (
            <li key={item.key} className="inline-flex items-center gap-1.5" data-legend={item.kind}>
              <LegendSwatch item={item} />
              {item.label}
            </li>
          ))}
        </ul>
      )}

      {model.notices.length > 0 && (
        <ul className="space-y-0.5 text-xs text-[var(--2a-text-secondary)]" data-chart="notices">
          {model.notices.map((n) => (
            <li key={n.key} data-notice={n.key}>
              {n.text}
            </li>
          ))}
        </ul>
      )}

      <TrendsTable rows={model.table} />
    </div>
  );
}

function LegendSwatch({ item }) {
  if (item.kind === "median" || item.kind === "keyDate") {
    return (
      <svg width={18} height={8} aria-hidden="true">
        <line x1={0} x2={18} y1={4} y2={4} stroke={item.color} strokeWidth={1.5} strokeDasharray={item.kind === "median" ? "5 3" : "2 3"} />
      </svg>
    );
  }
  if (item.kind === "bigMove") {
    return (
      <svg width={10} height={10} aria-hidden="true">
        <circle cx={5} cy={5} r={3} fill="#FFFFFF" stroke={item.color} strokeWidth={1.5} />
      </svg>
    );
  }
  if (item.kind === "outlier") {
    return (
      <svg width={18} height={8} aria-hidden="true">
        <line x1={0} x2={18} y1={4} y2={4} stroke={item.color} strokeWidth={2} />
      </svg>
    );
  }
  return (
    <span
      aria-hidden="true"
      className="inline-block h-2.5 w-3.5 rounded-sm"
      style={{ background: item.color, opacity: item.kind === "band" ? 0.2 : 0.8 }}
    />
  );
}
