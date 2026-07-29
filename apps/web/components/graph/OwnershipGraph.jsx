"use client";

/**
 * OwnershipGraph — the single shared interactive ownership/beneficiary tree.
 *
 * Generalises the Sprint 15 HierarchyBuilder (its hand-rolled SVG pan/zoom/
 * collapse and `getSubtreeWidth`/`layoutNode` layout are reused verbatim) into
 * ONE component rendered by BOTH the staff route and the member route. What it
 * adds on top of HierarchyBuilder:
 *   - typed edges (ownership vs beneficiary), visually distinct + a legend;
 *     ownership edges label the %, beneficiary edges carry none
 *   - filters: entity type, relationship type (isolate ownership/beneficiary),
 *     and a minimum ownership-% threshold
 *   - time-travel via the shared <AsOfPicker> (Sprint 18 paradigm)
 *   - reverse / "owned-by" traversal toggle (flips direction server-side)
 *   - Attio-style clickable nodes that navigate to the entity's detail page
 *
 * It is purely a viewer: the data (already visibility- and restricted-filtered
 * server-side) is fetched from `apiBase`, which differs only by route:
 *   staff  → /api/entities/{id}/ownership-graph
 *   member → /api/me/ownership-graph
 *
 * Props:
 *   apiBase      — base path (no query string); controls append their params
 *   onNodeClick  — (entityId) => void, node navigation (Attio-style)
 *   title        — heading text
 *   emptyMessage — shown when the tree is empty for this viewer
 */
import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { createPortal } from "react-dom";
import AsOfPicker from "./AsOfPicker";
import OwnershipPrintDocument from "./OwnershipPrintDocument";
import { buildExportModel } from "@/lib/ownershipExport.mjs";

const ENTITY_COLORS = {
  individual: "#EEF4FF",
  trust: "#F0FDF4",
  llc: "#FFF7ED",
  household: "var(--2a-bg)",
  spv: "var(--2a-bg-sidebar)",
  foundation: "#FEF3F2",
  default: "#F8FAFC",
};

const NODE_W = 168;
const NODE_H = 54;
const H_GAP = 32;
const V_GAP = 82;

// ---------------------------------------------------------------------------
// Layout (reused from HierarchyBuilder)
// ---------------------------------------------------------------------------
function getSubtreeWidth(node, collapsed) {
  if (!node.children || node.children.length === 0 || collapsed.has(node.id)) {
    return NODE_W;
  }
  const childrenWidth = node.children.reduce(
    (sum, child) => sum + getSubtreeWidth(child, collapsed),
    0,
  );
  const totalGaps = (node.children.length - 1) * H_GAP;
  return Math.max(NODE_W, childrenWidth + totalGaps);
}

function layoutNode(node, x, y, collapsed, positions = {}) {
  positions[node.id] = { x, y };
  if (!node.children || node.children.length === 0 || collapsed.has(node.id)) {
    return positions;
  }
  const totalWidth = getSubtreeWidth(node, collapsed);
  let childX = x - totalWidth / 2;
  for (const child of node.children) {
    const childWidth = getSubtreeWidth(child, collapsed);
    layoutNode(child, childX + childWidth / 2, y + NODE_H + V_GAP, collapsed, positions);
    childX += childWidth + H_GAP;
  }
  return positions;
}

function computeSVGBounds(positions) {
  const xs = Object.values(positions).map((p) => p.x);
  const ys = Object.values(positions).map((p) => p.y);
  if (xs.length === 0) return { minX: 0, minY: 0, width: 400, height: 300 };
  return {
    minX: Math.min(...xs) - NODE_W / 2 - 40,
    minY: Math.min(...ys) - 40,
    width: Math.max(...xs) - Math.min(...xs) + NODE_W + 80,
    height: Math.max(...ys) - Math.min(...ys) + NODE_H + 80,
  };
}

function getVisibleNodes(node, collapsed, result = []) {
  result.push(node);
  if (!node.children || collapsed.has(node.id)) return result;
  for (const child of node.children) getVisibleNodes(child, collapsed, result);
  return result;
}

function getVisibleEdges(node, collapsed, result = []) {
  if (!node.children || collapsed.has(node.id)) return result;
  for (const child of node.children) {
    result.push({ parent: node, child });
    getVisibleEdges(child, collapsed, result);
  }
  return result;
}

function collectTypes(node, acc = new Set()) {
  if (!node) return acc;
  acc.add(node.entity_type);
  (node.children || []).forEach((c) => collectTypes(c, acc));
  return acc;
}

function collectCollapsible(node, depthThreshold, acc = new Set()) {
  if (!node) return acc;
  if (node.depth >= depthThreshold && node.children && node.children.length > 0) {
    acc.add(node.id);
  }
  (node.children || []).forEach((c) => collectCollapsible(c, depthThreshold, acc));
  return acc;
}

// Client-side filters. Dropping a node drops its subtree (a hidden parent makes
// its branch unreachable) — same discipline as the server-side prune.
function applyClientFilters(node, { types, minPct }, isRoot = true) {
  if (!node) return null;
  const keptChildren = [];
  for (const child of node.children || []) {
    // Entity-type filter (root is always kept so the focal never disappears).
    if (types && types.size > 0 && !types.has(child.entity_type)) continue;
    // Minimum ownership threshold — only meaningful for ownership edges.
    if (
      child.edge_type === "ownership" &&
      minPct > 0 &&
      Number(child.ownership_pct ?? 0) < minPct
    ) {
      continue;
    }
    const filtered = applyClientFilters(child, { types, minPct }, false);
    if (filtered) keptChildren.push(filtered);
  }
  return { ...node, children: keptChildren };
}

// ---------------------------------------------------------------------------
// Marks
// ---------------------------------------------------------------------------
function EdgePath({ child, parentPos, childPos }) {
  if (!parentPos || !childPos) return null;
  const x1 = parentPos.x;
  const y1 = parentPos.y + NODE_H;
  const x2 = childPos.x;
  const y2 = childPos.y;
  const midY = (y1 + y2) / 2;
  const d = `M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`;

  const isBeneficiary = child.edge_type === "beneficiary";
  const stroke = isBeneficiary ? "var(--2a-navy)" : "var(--2a-gold)";
  const dash = isBeneficiary ? "5 4" : undefined;
  const pct =
    !isBeneficiary && child.ownership_pct != null
      ? `${Number(child.ownership_pct).toFixed(1)}%`
      : null;
  const labelX = (x1 + x2) / 2;
  const labelY = midY;

  return (
    <g>
      <path
        d={d}
        fill="none"
        stroke={stroke}
        strokeWidth={1.5}
        strokeDasharray={dash}
        opacity={isBeneficiary ? 0.75 : 1}
      />
      {pct && (
        <>
          <rect x={labelX - 17} y={labelY - 9} width={34} height={15} rx={3} fill="#fff" stroke="#ece8dd" />
          <text
            x={labelX}
            y={labelY + 2}
            textAnchor="middle"
            fontSize={11}
            fontFamily="'Hanken Grotesk', system-ui, sans-serif"
            fill="var(--2a-gold)"
            fontWeight={600}
          >
            {pct}
          </text>
        </>
      )}
    </g>
  );
}

function TreeNode({ node, position, isFocal, isCollapsed, hasChildren, onToggle, onNavigate }) {
  const bgColor = ENTITY_COLORS[node.entity_type] || ENTITY_COLORS.default;
  const borderColor = isFocal ? "var(--2a-gold)" : "#ece8dd";
  const borderWidth = isFocal ? 2 : 1;

  return (
    <g transform={`translate(${position.x - NODE_W / 2}, ${position.y})`}>
      <rect
        width={NODE_W}
        height={NODE_H}
        rx={6}
        ry={6}
        fill={bgColor}
        stroke={borderColor}
        strokeWidth={borderWidth}
        style={{ cursor: "pointer" }}
        onClick={(e) => {
          e.stopPropagation();
          onNavigate(node.id);
        }}
      >
        <title>Open {node.display_name}</title>
      </rect>
      <text
        x={NODE_W / 2}
        y={20}
        textAnchor="middle"
        fontSize={13}
        fontWeight="600"
        fontFamily="'Hanken Grotesk', system-ui, sans-serif"
        fill="var(--2a-navy)"
        clipPath={`url(#clip-${node.id})`}
        style={{ pointerEvents: "none" }}
      >
        {node.display_name}
      </text>
      <text
        x={NODE_W / 2}
        y={37}
        textAnchor="middle"
        fontSize={10}
        fontFamily="'Hanken Grotesk', system-ui, sans-serif"
        fill="var(--2a-gold)"
        fontWeight="600"
        style={{ textTransform: "uppercase", letterSpacing: "0.06em", pointerEvents: "none" }}
      >
        {node.entity_type}
      </text>
      {hasChildren && (
        <g
          transform={`translate(${NODE_W - 18}, ${NODE_H / 2 - 7})`}
          onClick={(e) => {
            e.stopPropagation();
            onToggle(node.id);
          }}
          style={{ cursor: "pointer" }}
        >
          <rect width={14} height={14} rx={3} fill="var(--2a-navy)" opacity={0.1} />
          <text
            x={7}
            y={11}
            textAnchor="middle"
            fontSize={11}
            fontFamily="'Hanken Grotesk', system-ui, sans-serif"
            fill="var(--2a-navy)"
            fontWeight="700"
          >
            {isCollapsed ? "+" : "−"}
          </text>
        </g>
      )}
      <defs>
        <clipPath id={`clip-${node.id}`}>
          <rect x={8} y={0} width={NODE_W - 30} height={NODE_H} />
        </clipPath>
      </defs>
    </g>
  );
}

// ---------------------------------------------------------------------------
// Toolbar bits
// ---------------------------------------------------------------------------
const chipBase = {
  fontSize: 12,
  fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
  fontWeight: 600,
  borderRadius: 999,
  padding: "4px 12px",
  cursor: "pointer",
  border: "1px solid #ece8dd",
  letterSpacing: "0.02em",
};

function Chip({ active, activeBg = "var(--2a-navy)", activeColor = "var(--2a-gold-light)", onClick, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        ...chipBase,
        background: active ? activeBg : "#fff",
        color: active ? activeColor : "var(--2a-text-secondary)",
      }}
    >
      {children}
    </button>
  );
}

function Legend() {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <svg width={28} height={10}>
          <line x1={0} y1={5} x2={28} y2={5} stroke="var(--2a-gold)" strokeWidth={2} />
        </svg>
        <span style={{ fontSize: 12, fontFamily: "'Hanken Grotesk', system-ui, sans-serif", color: "var(--2a-text-secondary)" }}>
          Ownership (%)
        </span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <svg width={28} height={10}>
          <line x1={0} y1={5} x2={28} y2={5} stroke="var(--2a-navy)" strokeWidth={2} strokeDasharray="5 4" />
        </svg>
        <span style={{ fontSize: 12, fontFamily: "'Hanken Grotesk', system-ui, sans-serif", color: "var(--2a-text-secondary)" }}>
          Beneficiary
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------
export default function OwnershipGraph({ apiBase, onNodeClick, title = "Ownership Tree", emptyMessage }) {
  // Server-side controls (drive a refetch).
  const [direction, setDirection] = useState("owns");
  const [edgeTypes, setEdgeTypes] = useState(new Set(["ownership", "beneficiary"]));
  const [asOf, setAsOf] = useState("");

  // Client-side view controls.
  const [typeFilter, setTypeFilter] = useState(null); // null = all types
  const [minPct, setMinPct] = useState(0);
  const [collapsed, setCollapsed] = useState(new Set());
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(1);

  // Export / print (Sprint ownershiptreeb). The model is built from the SAME
  // client-filtered tree this component is already rendering — never a re-query
  // — so a restricted entity absent from the live view is absent from the
  // export by construction. `mounted` gates the body-level print portal for SSR.
  const [printModel, setPrintModel] = useState(null);
  const [mounted, setMounted] = useState(false);

  // Data.
  const [tree, setTree] = useState(null);
  const [focalId, setFocalId] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const isDragging = useRef(false);
  const dragStart = useRef({ x: 0, y: 0 });
  const panStart = useRef({ x: 0, y: 0 });
  const svgContainerRef = useRef(null);

  const edgeTypesKey = [...edgeTypes].sort().join(",");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      params.set("direction", direction);
      params.set("edge_types", edgeTypesKey || "ownership,beneficiary");
      if (asOf) params.set("as_of", asOf);
      const res = await fetch(`${apiBase}?${params.toString()}`, { cache: "no-store" });
      if (!res.ok) {
        setTree(null);
        setError(res.status === 403 ? "You do not have access to this ownership tree." : "Failed to load ownership tree.");
        return;
      }
      const data = await res.json();
      setTree(data.tree || null);
      setFocalId(data.focal_entity_id || null);
      // Collapse beyond the first two levels by default on a larger tree.
      setCollapsed(data.tree ? collectCollapsible(data.tree, 2) : new Set());
    } catch {
      setTree(null);
      setError("Network error loading ownership tree.");
    } finally {
      setLoading(false);
    }
  }, [apiBase, direction, edgeTypesKey, asOf]);

  useEffect(() => {
    load();
  }, [load]);

  const toggleCollapse = useCallback((id) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }, []);

  const toggleEdgeType = useCallback((t) => {
    setEdgeTypes((prev) => {
      const next = new Set(prev);
      if (next.has(t)) {
        if (next.size > 1) next.delete(t); // never allow zero edge types
      } else {
        next.add(t);
      }
      return next;
    });
  }, []);

  const handleMouseDown = useCallback(
    (e) => {
      if (e.button !== 0) return;
      if (e.target.tagName === "svg" || e.target.tagName === "SVG") {
        isDragging.current = true;
        dragStart.current = { x: e.clientX, y: e.clientY };
        panStart.current = { ...pan };
        e.preventDefault();
      }
    },
    [pan],
  );
  const handleMouseMove = useCallback((e) => {
    if (!isDragging.current) return;
    setPan({
      x: panStart.current.x + (e.clientX - dragStart.current.x),
      y: panStart.current.y + (e.clientY - dragStart.current.y),
    });
  }, []);
  const handleMouseUp = useCallback(() => {
    isDragging.current = false;
  }, []);
  const handleWheel = useCallback((e) => {
    e.preventDefault();
    const delta = e.deltaY > 0 ? -0.1 : 0.1;
    setZoom((z) => Math.min(3, Math.max(0.3, z + delta)));
  }, []);

  useEffect(() => {
    const el = svgContainerRef.current;
    if (!el) return;
    el.addEventListener("wheel", handleWheel, { passive: false });
    return () => el.removeEventListener("wheel", handleWheel);
  }, [handleWheel]);

  const navigate = useCallback(
    (id) => {
      if (onNodeClick) onNodeClick(id);
    },
    [onNodeClick],
  );

  useEffect(() => setMounted(true), []);

  const availableTypes = useMemo(() => (tree ? [...collectTypes(tree)] : []), [tree]);

  const viewTree = useMemo(
    () => (tree ? applyClientFilters(tree, { types: typeFilter, minPct }) : null),
    [tree, typeFilter, minPct],
  );

  // Build the print model from the current on-screen (filtered) tree, then hand
  // off to the browser's print dialog. `expandAll` overrides the collapsed set.
  const handleExport = useCallback(
    (expandAll) => {
      if (!viewTree) return;
      const today = new Date().toISOString().slice(0, 10);
      const model = buildExportModel(viewTree, {
        collapsed,
        expandAll,
        focalName: viewTree.display_name,
        asOf,
        today,
        generatedAt: new Date().toLocaleString(),
      });
      setPrintModel(model);
    },
    [viewTree, collapsed, asOf],
  );

  // Once the print document has rendered into the portal, open the print dialog
  // and clear the model when it closes (or is cancelled).
  useEffect(() => {
    if (!printModel || typeof window === "undefined") return;
    const clear = () => setPrintModel(null);
    window.addEventListener("afterprint", clear);
    const id = window.requestAnimationFrame(() => window.print());
    return () => {
      window.cancelAnimationFrame(id);
      window.removeEventListener("afterprint", clear);
    };
  }, [printModel]);

  const toggleType = (t) => {
    setTypeFilter((prev) => {
      const base = prev ?? new Set(availableTypes);
      const next = new Set(base);
      next.has(t) ? next.delete(t) : next.add(t);
      // All selected → back to null (meaning "all").
      if (next.size === availableTypes.length) return null;
      return next;
    });
  };
  const typeEnabled = (t) => typeFilter === null || typeFilter.has(t);

  const positions = viewTree ? layoutNode(viewTree, 0, 0, collapsed) : {};
  const visibleNodes = viewTree ? getVisibleNodes(viewTree, collapsed) : [];
  const visibleEdges = viewTree ? getVisibleEdges(viewTree, collapsed) : [];

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        background: "#fff",
        border: "1px solid #ece8dd",
        borderRadius: 8,
        overflow: "hidden",
        boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
      }}
    >
      {/* Toolbar */}
      <div style={{ padding: "16px 20px", borderBottom: "1px solid #ece8dd", background: "#fff" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12, marginBottom: 12 }}>
          <div style={{ fontFamily: "'Spectral', Georgia, serif", fontSize: 20, fontWeight: 500, color: "var(--2a-navy)" }}>
            {title}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 18, flexWrap: "wrap" }}>
            <Legend />
            {viewTree && (
              <div style={{ display: "flex", gap: 6 }} className="ownership-graph-export-controls">
                <button
                  type="button"
                  onClick={() => handleExport(false)}
                  title="Export / print the current view (collapsed branches stay collapsed)"
                  style={{
                    background: "var(--2a-navy)",
                    color: "var(--2a-gold-light)",
                    border: "none",
                    borderRadius: 5,
                    padding: "6px 12px",
                    fontSize: 12,
                    fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
                    fontWeight: 600,
                    cursor: "pointer",
                    letterSpacing: "0.04em",
                  }}
                >
                  Export / Print
                </button>
                <button
                  type="button"
                  onClick={() => handleExport(true)}
                  title="Expand every branch, then export / print the full tree"
                  style={{
                    background: "#fff",
                    color: "var(--2a-text-secondary)",
                    border: "1px solid #ece8dd",
                    borderRadius: 5,
                    padding: "6px 12px",
                    fontSize: 12,
                    fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
                    fontWeight: 600,
                    cursor: "pointer",
                  }}
                >
                  Expand all &amp; print
                </button>
              </div>
            )}
          </div>
        </div>

        <div style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "center" }}>
          {/* Direction / reverse toggle */}
          <div style={{ display: "flex", gap: 6 }}>
            <Chip active={direction === "owns"} onClick={() => setDirection("owns")}>
              Owns ▾
            </Chip>
            <Chip active={direction === "owned_by"} onClick={() => setDirection("owned_by")}>
              Owned by ▴
            </Chip>
          </div>

          {/* Relationship-type filter */}
          <div style={{ display: "flex", gap: 6 }}>
            <Chip active={edgeTypes.has("ownership")} activeBg="var(--2a-gold)" activeColor="var(--2a-navy)" onClick={() => toggleEdgeType("ownership")}>
              Ownership
            </Chip>
            <Chip active={edgeTypes.has("beneficiary")} onClick={() => toggleEdgeType("beneficiary")}>
              Beneficiary
            </Chip>
          </div>

          {/* Minimum ownership % */}
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ fontSize: 11, fontFamily: "'Hanken Grotesk', system-ui, sans-serif", fontWeight: 700, color: "var(--2a-text-muted)", textTransform: "uppercase", letterSpacing: "0.12em" }}>
              Min %
            </span>
            <input
              type="number"
              min={0}
              max={100}
              step={1}
              value={minPct}
              onChange={(e) => setMinPct(Number(e.target.value) || 0)}
              style={{
                width: 64,
                borderRadius: 5,
                border: "1px solid var(--2a-border)",
                padding: "5px 8px",
                fontSize: 13,
                fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
                color: "var(--2a-text)",
              }}
            />
          </div>

          <AsOfPicker value={asOf} onApply={setAsOf} />
        </div>

        {/* Entity-type filter */}
        {availableTypes.length > 1 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 12, alignItems: "center" }}>
            <span style={{ fontSize: 11, fontFamily: "'Hanken Grotesk', system-ui, sans-serif", fontWeight: 700, color: "var(--2a-text-muted)", textTransform: "uppercase", letterSpacing: "0.12em", marginRight: 4 }}>
              Types
            </span>
            {availableTypes.map((t) => (
              <Chip key={t} active={typeEnabled(t)} activeBg="var(--2a-bg-sidebar)" activeColor="var(--2a-navy)" onClick={() => toggleType(t)}>
                {t}
              </Chip>
            ))}
          </div>
        )}
      </div>

      {/* Canvas */}
      <div
        ref={svgContainerRef}
        style={{
          position: "relative",
          minHeight: 520,
          background: "var(--2a-bg)",
          cursor: isDragging.current ? "grabbing" : "grab",
          overflow: "hidden",
        }}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
      >
        {/* Zoom controls */}
        <div style={{ position: "absolute", top: 12, right: 12, zIndex: 10, display: "flex", flexDirection: "column", gap: 4 }}>
          {[
            { label: "+", onClick: () => setZoom((z) => Math.min(3, z + 0.15)) },
            { label: "−", onClick: () => setZoom((z) => Math.max(0.3, z - 0.15)) },
            { label: "⌂", onClick: () => { setPan({ x: 0, y: 0 }); setZoom(1); } },
          ].map((b) => (
            <button
              key={b.label}
              onClick={b.onClick}
              style={{
                width: 28,
                height: 28,
                background: "#fff",
                border: "1px solid #ece8dd",
                borderRadius: 5,
                fontSize: 15,
                fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
                color: "var(--2a-navy)",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
              }}
            >
              {b.label}
            </button>
          ))}
        </div>

        {loading ? (
          <div style={{ padding: 40, fontFamily: "'Hanken Grotesk', system-ui, sans-serif", fontSize: 14, color: "var(--2a-text-muted)" }}>
            Loading ownership tree…
          </div>
        ) : error ? (
          <div style={{ padding: 40, fontFamily: "'Hanken Grotesk', system-ui, sans-serif", fontSize: 14, color: "var(--2a-error)" }}>
            {error}
          </div>
        ) : !viewTree ? (
          <div style={{ padding: 40, fontFamily: "'Hanken Grotesk', system-ui, sans-serif", fontSize: 14, color: "var(--2a-text-muted)" }}>
            {emptyMessage || "No ownership tree data available."}
          </div>
        ) : (
          <svg width="100%" height="100%" style={{ display: "block", minHeight: 520 }}>
            <g
              transform={`translate(${pan.x + (svgContainerRef.current?.offsetWidth ?? 600) / 2}, ${pan.y + 48}) scale(${zoom})`}
              style={{ transformOrigin: "0 0" }}
            >
              {visibleEdges.map(({ parent, child }) => (
                <EdgePath key={`${parent.id}-${child.id}`} child={child} parentPos={positions[parent.id]} childPos={positions[child.id]} />
              ))}
              {visibleNodes.map((node) => {
                const pos = positions[node.id];
                if (!pos) return null;
                return (
                  <TreeNode
                    key={node.id}
                    node={node}
                    position={pos}
                    isFocal={node.id === focalId}
                    isCollapsed={collapsed.has(node.id)}
                    hasChildren={!!(node.children && node.children.length)}
                    onToggle={toggleCollapse}
                    onNavigate={navigate}
                  />
                );
              })}
            </g>
          </svg>
        )}
      </div>

      {/* Hide the interactive card entirely when printing — the dedicated
          print document (rendered into a body-level portal below) takes over. */}
      <style>{`
        @media print {
          .ownership-graph-export-controls { display: none !important; }
        }
      `}</style>

      {/* Dedicated print renderer, portaled to <body> so the @media print rule
          that hides every other top-level node can reveal just this document. */}
      {mounted &&
        printModel &&
        createPortal(
          <div className="ownership-export-portal">
            <OwnershipPrintDocument model={printModel} focalId={focalId} />
          </div>,
          document.body,
        )}
    </div>
  );
}
