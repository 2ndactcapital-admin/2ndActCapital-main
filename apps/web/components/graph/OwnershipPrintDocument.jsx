"use client";

/**
 * OwnershipPrintDocument — the DEDICATED print-optimised renderer (Sprint
 * ownershiptreeb, Task 2). This is a genuinely separate layout path, NOT CSS
 * over the interactive DOM (see lib/ownershipExport.mjs for why the print-
 * stylesheet approach was rejected in Task 1). It renders the paginated model
 * produced by `buildExportModel` as a sequence of fixed-size, full-scale (no
 * shrink) pages, each with a running header band, a legend on page 1, and a
 * static SVG of that page's subtree.
 *
 * It consumes the SAME already-visibility-/restricted-filtered tree the
 * interactive <OwnershipGraph> is rendering — the model is built from that
 * component's own state, never a re-query — so anything absent from the live
 * view is absent here by construction.
 *
 * On screen it is hidden; only `window.print()` reveals it, and the @media
 * print block hides the rest of the app and page-breaks between pages.
 */
import {
  NODE_W,
  NODE_H,
  HEADER_H,
  layoutNode,
  computeBounds,
} from "@/lib/ownershipExport.mjs";

const ENTITY_COLORS = {
  individual: "#EEF4FF",
  trust: "#F0FDF4",
  llc: "#FFF7ED",
  household: "var(--2a-bg)",
  spv: "var(--2a-bg-sidebar)",
  foundation: "#FEF3F2",
  default: "#F8FAFC",
};

const FONT = "'Hanken Grotesk', system-ui, sans-serif";
const SERIF = "'Spectral', Georgia, serif";

function PrintEdge({ child, parentPos, childPos }) {
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
      <path d={d} fill="none" stroke={stroke} strokeWidth={1.5} strokeDasharray={dash} opacity={isBeneficiary ? 0.75 : 1} />
      {pct && (
        <>
          <rect x={labelX - 17} y={labelY - 9} width={34} height={15} rx={3} fill="#fff" stroke="#ece8dd" />
          <text x={labelX} y={labelY + 2} textAnchor="middle" fontSize={11} fontFamily={FONT} fill="var(--2a-gold)" fontWeight={600}>
            {pct}
          </text>
        </>
      )}
    </g>
  );
}

function PrintNode({ node, position, isFocal }) {
  const bgColor = ENTITY_COLORS[node.entity_type] || ENTITY_COLORS.default;
  const borderColor = isFocal ? "var(--2a-gold)" : "#ece8dd";
  const borderWidth = isFocal ? 2 : 1;
  return (
    <g transform={`translate(${position.x - NODE_W / 2}, ${position.y})`}>
      <rect width={NODE_W} height={NODE_H} rx={6} ry={6} fill={bgColor} stroke={borderColor} strokeWidth={borderWidth} />
      <text x={NODE_W / 2} y={20} textAnchor="middle" fontSize={13} fontWeight="600" fontFamily={FONT} fill="var(--2a-navy)" clipPath={`url(#pclip-${node.id})`}>
        {node.display_name}
      </text>
      <text x={NODE_W / 2} y={37} textAnchor="middle" fontSize={10} fontFamily={FONT} fill="var(--2a-gold)" fontWeight="600" style={{ textTransform: "uppercase", letterSpacing: "0.06em" }}>
        {node.entity_type}
      </text>
      <defs>
        <clipPath id={`pclip-${node.id}`}>
          <rect x={8} y={0} width={NODE_W - 16} height={NODE_H} />
        </clipPath>
      </defs>
    </g>
  );
}

function collectNodes(node, acc = []) {
  if (!node) return acc;
  acc.push(node);
  (node.children || []).forEach((c) => collectNodes(c, acc));
  return acc;
}
function collectEdges(node, acc = []) {
  for (const child of node.children || []) {
    acc.push({ parent: node, child });
    collectEdges(child, acc);
  }
  return acc;
}

function LegendBand() {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <svg width={28} height={10}>
          <line x1={0} y1={5} x2={28} y2={5} stroke="var(--2a-gold)" strokeWidth={2} />
        </svg>
        <span style={{ fontSize: 11, fontFamily: FONT, color: "var(--2a-text-secondary)" }}>Ownership (%) — who owns whom</span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <svg width={28} height={10}>
          <line x1={0} y1={5} x2={28} y2={5} stroke="var(--2a-navy)" strokeWidth={2} strokeDasharray="5 4" />
        </svg>
        <span style={{ fontSize: 11, fontFamily: FONT, color: "var(--2a-text-secondary)" }}>Beneficiary — who benefits, no percentage</span>
      </div>
    </div>
  );
}

function PageBand({ header, page, pageCount, focalId }) {
  const rootName = page.root?.display_name;
  return (
    <div style={{ height: HEADER_H, borderBottom: "1px solid #ece8dd", padding: "8px 4px", display: "flex", flexDirection: "column", justifyContent: "center", gap: 2 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <div style={{ fontFamily: SERIF, fontSize: 18, fontWeight: 500, color: "var(--2a-navy)" }}>
          {header.focalName}
          {page.root && page.root.id !== focalId && (
            <span style={{ fontFamily: FONT, fontSize: 12, color: "var(--2a-text-muted)", marginLeft: 8 }}>
              › {rootName}
            </span>
          )}
        </div>
        <div style={{ fontFamily: FONT, fontSize: 11, color: "var(--2a-text-muted)" }}>
          Page {page.index} of {pageCount}
        </div>
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ fontFamily: FONT, fontSize: 12, color: "var(--2a-text-secondary)" }}>
          Ownership structure as of {header.asOfLabel}
          {header.isHistorical && (
            <span style={{ marginLeft: 8, color: "var(--2a-gold)", fontWeight: 600 }}>· historical view</span>
          )}
        </div>
        {page.index === 1 ? (
          <LegendBand />
        ) : (
          <span style={{ fontFamily: FONT, fontSize: 11, color: "var(--2a-text-muted)" }}>Generated {header.generatedAt}</span>
        )}
      </div>
    </div>
  );
}

function TreePage({ model, page, focalId }) {
  const positions = layoutNode(page.root, 0, 0, {});
  const bounds = computeBounds(positions);
  const nodes = collectNodes(page.root);
  const edges = collectEdges(page.root);
  return (
    <section className="ownership-export-page">
      <PageBand header={model.header} page={page} pageCount={model.pageCount} focalId={focalId} />
      <div style={{ paddingTop: 12 }}>
        <svg
          width={bounds.width}
          height={bounds.height}
          viewBox={`${bounds.minX} ${bounds.minY} ${bounds.width} ${bounds.height}`}
          style={{ maxWidth: "100%", display: "block", margin: "0 auto" }}
        >
          {edges.map(({ parent, child }) => (
            <PrintEdge key={`${parent.id}-${child.id}`} child={child} parentPos={positions[parent.id]} childPos={positions[child.id]} />
          ))}
          {nodes.map((n) => (
            <PrintNode key={n.id} node={n} position={positions[n.id]} isFocal={n.id === focalId} />
          ))}
        </svg>
      </div>
      {page.index === 1 && (
        <div style={{ marginTop: 14, paddingTop: 10, borderTop: "1px solid #ece8dd", fontFamily: FONT, fontSize: 10, color: "var(--2a-text-muted)" }}>
          Generated {model.header.generatedAt}. {model.expandAll ? "Full tree (expanded)." : "Reflects the branches expanded at export time."} 2nd Act Capital — confidential.
        </div>
      )}
    </section>
  );
}

export default function OwnershipPrintDocument({ model, focalId }) {
  if (!model) return null;
  return (
    <>
      <div className="ownership-export-root" aria-hidden>
        {model.pages.length === 0 ? (
          <section className="ownership-export-page">
            <div style={{ fontFamily: FONT, fontSize: 14, color: "var(--2a-text-muted)" }}>
              Nothing to export for this view.
            </div>
          </section>
        ) : (
          model.pages.map((page) => (
            <TreePage key={page.index} model={model} page={page} focalId={focalId} />
          ))
        )}
      </div>
      <style>{`
        .ownership-export-root { display: none; }
        @media print {
          @page { size: landscape; margin: 0.5in; }
          html, body { background: #fff !important; }
          body > *:not(.ownership-export-portal) { display: none !important; }
          .ownership-export-portal { display: block !important; }
          .ownership-export-root { display: block !important; }
          .ownership-export-page {
            break-after: page;
            page-break-after: always;
            box-sizing: border-box;
            width: 100%;
          }
          .ownership-export-page:last-child { break-after: auto; page-break-after: avoid; }
        }
      `}</style>
    </>
  );
}
