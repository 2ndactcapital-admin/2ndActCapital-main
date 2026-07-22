"use client";

import { useEffect, useRef, useState, useCallback } from "react";

// ── State color palette ──────────────────────────────────────────────────────
const STATE_COLORS = {
  none:     { base: "#F4F1E9", light: "#FAF8F3", text: "var(--2a-text-secondary)" },
  under:    { base: "#C9A24B", light: "#D4B06A", text: "var(--2a-navy)" },
  on:       { base: "#2F6B4F", light: "#3D7F60", text: "var(--2a-bg)" },
  over:     { base: "#7E2B2B", light: "#933636", text: "var(--2a-bg)" },
  off_plan: { base: "#3A3A3C", light: "#4A4A4C", text: "var(--2a-bg)" },
};

// State glyph for color-blind safety
const STATE_GLYPHS = {
  none: "",
  under: "↓",
  on: "✓",
  over: "↑",
  off_plan: "!",
};

const STATE_LABELS = {
  none: "Unallocated",
  under: "Under target",
  on: "On target",
  over: "Over target",
  off_plan: "Held, not in plan",
};

// Minimum slice fraction so every node is always visible
const MIN_FRAC = 0.003;
// Rings: inner radius, outer radius for each level
const HOLE_R = 28;
// These are fractions of the total radius (computed from SVG size)
const RING_FRACS = [
  { inner: 0.26, outer: 0.44 },  // super
  { inner: 0.46, outer: 0.66 },  // major
  { inner: 0.68, outer: 0.97 },  // sub
];

// ── Geometry helpers ─────────────────────────────────────────────────────────

function polarToXY(angle, r) {
  return [r * Math.cos(angle), r * Math.sin(angle)];
}

function arcPath(startAngle, endAngle, innerR, outerR) {
  if (endAngle - startAngle < 0.0001) return "";
  const largeArc = endAngle - startAngle > Math.PI ? 1 : 0;
  const [ox1, oy1] = polarToXY(startAngle, outerR);
  const [ox2, oy2] = polarToXY(endAngle, outerR);
  const [ix1, iy1] = polarToXY(endAngle, innerR);
  const [ix2, iy2] = polarToXY(startAngle, innerR);
  return [
    `M ${ox1} ${oy1}`,
    `A ${outerR} ${outerR} 0 ${largeArc} 1 ${ox2} ${oy2}`,
    `L ${ix1} ${iy1}`,
    `A ${innerR} ${innerR} 0 ${largeArc} 0 ${ix2} ${iy2}`,
    "Z",
  ].join(" ");
}

function labelPoint(startAngle, endAngle, r) {
  const mid = (startAngle + endAngle) / 2;
  return polarToXY(mid, r);
}

function arcLength(startAngle, endAngle, r) {
  return Math.abs(endAngle - startAngle) * r;
}

function radialRoom(innerR, outerR) {
  return outerR - innerR;
}

// Shorten a label to fit in the arc
function shortenLabel(label, arcLen, radRoom, fontSize = 10) {
  const charsPerPixel = fontSize * 0.6;
  const maxCharsW = Math.floor(arcLen / charsPerPixel);
  const maxLines = Math.floor(radRoom / (fontSize * 1.3));
  const maxChars = maxCharsW * Math.max(1, maxLines);
  if (label.length <= maxCharsW) return { lines: [label], fits: true };
  if (maxLines >= 2 && label.length <= maxCharsW * 2) {
    // Try two-line split
    const words = label.split(" ");
    let line1 = "";
    let line2 = "";
    for (const w of words) {
      if ((line1 + " " + w).trim().length <= maxCharsW) line1 = (line1 + " " + w).trim();
      else line2 = (line2 + " " + w).trim();
    }
    if (line1 && line2 && line2.length <= maxCharsW)
      return { lines: [line1, line2], fits: true };
  }
  if (label.length <= maxChars) return { lines: [label.slice(0, maxCharsW)], fits: false };
  return { lines: [], fits: false };
}

// ── Angle computation ─────────────────────────────────────────────────────────

const START_ANGLE = -Math.PI / 2; // Top of circle

function computeArcs(superClasses, zoomedKey) {
  const allArcs = { super: [], major: [], sub: [] };
  const TOTAL_ANGLE = 2 * Math.PI;

  // If zoomed into a super-class, only show that wedge filling the full circle
  let filteredSC = superClasses;
  let zoomedSC = null;
  if (zoomedKey) {
    zoomedSC = superClasses.find(sc => sc.key === zoomedKey);
    filteredSC = zoomedSC ? [zoomedSC] : superClasses;
  }

  // Total target for the visible super-classes (with floor)
  const totalTarget = filteredSC.reduce(
    (s, sc) => s + Math.max(sc.target_pct, MIN_FRAC * 100), 0
  ) || 1;

  let scAngle = START_ANGLE;
  for (const sc of filteredSC) {
    const scSpan = (Math.max(sc.target_pct, MIN_FRAC * 100) / totalTarget) * TOTAL_ANGLE;
    const scStart = scAngle;
    const scEnd = scAngle + scSpan;
    allArcs.super.push({ ...sc, startAngle: scStart, endAngle: scEnd });

    // Major classes within this super-class
    const totalMC = sc.major_classes.reduce(
      (s, mc) => s + Math.max(mc.target_pct, MIN_FRAC * 100), 0
    ) || 1;
    let mcAngle = scStart;
    for (const mc of sc.major_classes) {
      const mcSpan = ((Math.max(mc.target_pct, MIN_FRAC * 100) / totalMC) * scSpan);
      const mcStart = mcAngle;
      const mcEnd = mcAngle + mcSpan;
      allArcs.major.push({
        ...mc,
        startAngle: mcStart,
        endAngle: mcEnd,
        parentKey: sc.key,
      });

      // Sub-categories within this major-class
      const totalSub = mc.sub_categories.reduce(
        (s, sub) => s + Math.max(sub.target_pct, MIN_FRAC * 100), 0
      ) || 1;
      let subAngle = mcStart;
      for (const sub of mc.sub_categories) {
        const subSpan = ((Math.max(sub.target_pct, MIN_FRAC * 100) / totalSub) * mcSpan);
        allArcs.sub.push({
          ...sub,
          startAngle: subAngle,
          endAngle: subAngle + subSpan,
          parentKey: mc.key,
          grandParentKey: sc.key,
        });
        subAngle += subSpan;
      }
      mcAngle += mcSpan;
    }
    scAngle += scSpan;
  }
  return allArcs;
}

// ── Tooltip ──────────────────────────────────────────────────────────────────
function Tooltip({ node, x, y, size }) {
  if (!node) return null;
  const { label, actual_pct, target_pct, state, labelShown } = node;
  const color = STATE_COLORS[state] || STATE_COLORS.none;
  const glyph = STATE_GLYPHS[state] || "";
  const stateLabel = STATE_LABELS[state] || "";
  const actualStr = actual_pct != null ? actual_pct.toFixed(1) + "%" : "–";
  const targetStr = target_pct != null ? target_pct.toFixed(1) + "%" : "–";

  const lines = labelShown
    ? [`${actualStr} actual / ${targetStr} target`, `${glyph} ${stateLabel}`]
    : [label, `${actualStr} actual / ${targetStr} target`, `${glyph} ${stateLabel}`];

  return (
    <div
      style={{
        position: "fixed",
        left: x + 14,
        top: y - 8,
        background: "var(--2a-navy)",
        color: "var(--2a-bg)",
        borderRadius: 6,
        padding: "8px 12px",
        fontSize: 12,
        lineHeight: 1.55,
        pointerEvents: "none",
        zIndex: 9999,
        maxWidth: 220,
        boxShadow: "0 2px 8px rgba(0,0,0,0.25)",
        whiteSpace: "pre",
      }}
    >
      {lines.map((l, i) => (
        <div key={i} style={i === 0 && !labelShown ? { fontWeight: 600 } : {}}>{l}</div>
      ))}
    </div>
  );
}

// ── Legend ───────────────────────────────────────────────────────────────────
function Legend() {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: "12px 20px", justifyContent: "center", marginTop: 16 }}>
      {Object.entries(STATE_LABELS).map(([state, label]) => {
        const c = STATE_COLORS[state];
        return (
          <div key={state} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--2a-text-secondary)" }}>
            <span
              style={{
                display: "inline-block",
                width: 12,
                height: 12,
                borderRadius: 3,
                background: c.base,
                border: state === "none" ? "1px solid var(--2a-border)" : "none",
              }}
            />
            <span>{STATE_GLYPHS[state] && <b>{STATE_GLYPHS[state]} </b>}{label}</span>
          </div>
        );
      })}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────
export default function AllocationSunburst({ data, size = 540 }) {
  const svgRef = useRef(null);
  const [tooltip, setTooltip] = useState(null);
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 });
  const [zoomedKey, setZoomedKey] = useState(null);
  const [drillPath, setDrillPath] = useState([]);

  const superClasses = data?.super_classes || [];
  const totalDollar = data?.total_actual_dollar || 0;

  const center = size / 2;
  const maxR = size / 2 - 4;

  const rings = RING_FRACS.map(f => ({
    inner: HOLE_R + (maxR - HOLE_R) * f.inner,
    outer: HOLE_R + (maxR - HOLE_R) * f.outer,
  }));

  const arcs = computeArcs(superClasses, zoomedKey);

  const handleMouseMove = useCallback((e) => {
    setMousePos({ x: e.clientX, y: e.clientY });
  }, []);

  const handleSCClick = useCallback((sc) => {
    if (zoomedKey === sc.key) {
      // Already zoomed — zoom out
      setZoomedKey(null);
      setDrillPath([]);
    } else {
      setZoomedKey(sc.key);
      setDrillPath([{ key: sc.key, label: sc.label }]);
    }
  }, [zoomedKey]);

  const handleZoomOut = () => {
    setZoomedKey(null);
    setDrillPath([]);
  };

  // Gradient definitions
  const gradientDefs = Object.entries(STATE_COLORS).map(([state, c]) => (
    <radialGradient key={state} id={`grad-${state}`} cx="50%" cy="50%" r="50%">
      <stop offset="0%" stopColor={c.light} />
      <stop offset="100%" stopColor={c.base} />
    </radialGradient>
  ));

  // Render arcs for one level
  function renderRing(arcList, ringIdx, level) {
    const ring = rings[ringIdx];
    return arcList.map((arc) => {
      const d = arcPath(arc.startAngle, arc.endAngle, ring.inner, ring.outer);
      if (!d) return null;
      const state = arc.state || "none";
      const color = STATE_COLORS[state];
      const midAngle = (arc.startAngle + arc.endAngle) / 2;
      const arcLen = arcLength(arc.startAngle, arc.endAngle, (ring.inner + ring.outer) / 2);
      const radRoom = ring.outer - ring.inner;
      const fontSize = level === "sub" ? 9 : level === "major" ? 10 : 11;
      const { lines, fits } = shortenLabel(arc.label, arcLen - 8, radRoom - 8, fontSize);
      const showLabel = lines.length > 0 && arcLen > 28 && radRoom > 16;
      const [lx, ly] = labelPoint(arc.startAngle, arc.endAngle, (ring.inner + ring.outer) / 2);
      const textColor = color.text;
      const glyph = STATE_GLYPHS[state];

      return (
        <g
          key={arc.key}
          style={{ cursor: level === "super" ? "pointer" : "default" }}
          onClick={level === "super" ? () => handleSCClick(arc) : undefined}
          onMouseEnter={() => setTooltip({ ...arc, labelShown: showLabel })}
          onMouseLeave={() => setTooltip(null)}
          aria-label={`${arc.label}: ${arc.actual_pct?.toFixed(1)}% actual, ${arc.target_pct?.toFixed(1)}% target, ${STATE_LABELS[state]}`}
        >
          <path
            d={d}
            fill={`url(#grad-${state})`}
            stroke="var(--2a-bg-card)"
            strokeWidth={level === "super" ? 1.5 : 0.75}
          />
          {showLabel && (
            <text
              x={lx}
              y={ly}
              textAnchor="middle"
              dominantBaseline="middle"
              fill={textColor}
              fontSize={fontSize}
              fontFamily="'Hanken Grotesk', system-ui, sans-serif"
              fontWeight={level === "super" ? 600 : 400}
              style={{ pointerEvents: "none", userSelect: "none" }}
            >
              {lines.map((line, i) => (
                <tspan
                  key={i}
                  x={lx}
                  dy={i === 0 ? (lines.length > 1 ? `-${(fontSize * 0.65 * (lines.length - 1)).toFixed(0)}` : "0") : fontSize * 1.3}
                >
                  {line}{i === lines.length - 1 && glyph ? ` ${glyph}` : ""}
                </tspan>
              ))}
            </text>
          )}
        </g>
      );
    });
  }

  // Super-class black dividers (from inner ring edge to outer rim)
  function renderDividers() {
    if (zoomedKey) return null; // No dividers when zoomed
    return arcs.super.map((arc) => {
      const innerR = rings[0].inner;
      const outerR = rings[2].outer;
      const [x1, y1] = polarToXY(arc.startAngle, innerR);
      const [x2, y2] = polarToXY(arc.startAngle, outerR);
      return (
        <line
          key={`div-${arc.key}`}
          x1={x1} y1={y1} x2={x2} y2={y2}
          stroke="var(--2a-navy)"
          strokeWidth={1.5}
          style={{ pointerEvents: "none" }}
        />
      );
    });
  }

  const fmtDollar = (v) => {
    if (!v) return "$0";
    if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
    if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
    if (v >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
    return `$${v.toFixed(0)}`;
  };

  return (
    <div
      style={{ display: "flex", flexDirection: "column", alignItems: "center" }}
      onMouseMove={handleMouseMove}
    >
      {/* Drill-down breadcrumb */}
      {drillPath.length > 0 && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12, fontSize: 13, color: "var(--2a-text-muted)" }}>
          <button
            onClick={handleZoomOut}
            style={{
              background: "none", border: "1px solid var(--2a-border)", borderRadius: 4,
              padding: "3px 10px", cursor: "pointer", color: "var(--2a-navy)", fontSize: 12,
              fontFamily: "inherit",
            }}
          >
            ← Full portfolio
          </button>
          {drillPath.map((crumb, i) => (
            <span key={crumb.key}>
              {i > 0 && <span style={{ margin: "0 4px" }}>/</span>}
              <span style={{ color: "var(--2a-navy)", fontWeight: 500 }}>{crumb.label}</span>
            </span>
          ))}
        </div>
      )}

      {/* SVG sunburst */}
      <svg
        ref={svgRef}
        width={size}
        height={size}
        viewBox={`${-center} ${-center} ${size} ${size}`}
        aria-label="Portfolio allocation sunburst"
        role="img"
        style={{ maxWidth: "100%", height: "auto" }}
      >
        <defs>{gradientDefs}</defs>

        {/* Sub (outer) ring */}
        {renderRing(arcs.sub, 2, "sub")}
        {/* Major (middle) ring */}
        {renderRing(arcs.major, 1, "major")}
        {/* Super (inner) ring */}
        {renderRing(arcs.super, 0, "super")}

        {/* Black dividers from inner-ring edge to outer rim */}
        <g>{renderDividers()}</g>

        {/* Center hole — label */}
        <circle cx={0} cy={0} r={HOLE_R} fill="var(--2a-bg-card)" />
        <text
          textAnchor="middle"
          dominantBaseline="middle"
          y={-5}
          fontSize={9}
          fill="var(--2a-text-muted)"
          fontFamily="'Hanken Grotesk', system-ui, sans-serif"
          fontWeight={600}
          textTransform="uppercase"
          letterSpacing="0.08em"
          style={{ userSelect: "none" }}
        >
          Allocation
        </text>
        {totalDollar > 0 && (
          <text
            textAnchor="middle"
            dominantBaseline="middle"
            y={7}
            fontSize={8}
            fill="var(--2a-nav-rest)"
            fontFamily="'Hanken Grotesk', system-ui, sans-serif"
            style={{ userSelect: "none" }}
          >
            {fmtDollar(totalDollar)}
          </text>
        )}
      </svg>

      {/* Legend */}
      <Legend />

      {/* Tooltip */}
      {tooltip && (
        <Tooltip node={tooltip} x={mousePos.x} y={mousePos.y} />
      )}
    </div>
  );
}
