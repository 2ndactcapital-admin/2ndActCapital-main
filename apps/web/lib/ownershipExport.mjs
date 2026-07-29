/**
 * Ownership-tree PRINT/EXPORT model (Sprint ownershiptreeb).
 *
 * Task 1's stress test found that a @media-print stylesheet layered over the
 * interactive <OwnershipGraph> DOM does NOT hold up on a large tree: the
 * interactive view is ONE `<svg>` inside an `overflow:hidden`, pan/zoom
 * container, and browsers do not page-break inside an SVG. A wide/deep tree
 * (25-30+ nodes) is therefore either clipped to the 520px container or
 * scale-to-fit shrunk by the browser to ~0.2x — illegible either way. So this
 * sprint builds a DEDICATED, print-optimised layout path that explicitly
 * paginates (one subtree per page) and never shrinks a node below scale 1.
 *
 * This module is the pure, framework-free core of that path: it takes the SAME
 * already-visibility-/restricted-filtered tree the interactive view is
 * rendering (never a re-query) and derives a paginated page model. It is kept
 * dependency-free (no React) so it can be unit-exercised directly by
 * verify_ownershiptreeb.py under plain node.
 */

// Node geometry — identical to the interactive OwnershipGraph so the export is
// visually faithful (same box size, same gaps).
export const NODE_W = 168;
export const NODE_H = 54;
export const H_GAP = 32;
export const V_GAP = 82;

// Printable content box per page. Landscape US Letter with 0.5in margins is
// ~10in x 7.5in => ~960 x 720 px @96dpi; HEADER_H reserves the running band we
// draw at the top of every page. A subtree that fits BOTH bounds renders at
// scale 1 (full-size, legible) with no browser shrink.
export const HEADER_H = 64;
export const MAX_PAGE_W = 960;
export const PAGE_TREE_H = 720 - HEADER_H; // usable tree height below the band

// ---------------------------------------------------------------------------
// Layout (mirrors the interactive component's getSubtreeWidth/layoutNode; here
// the input is an already-collapse-pruned "visible" tree, so no collapsed set).
// ---------------------------------------------------------------------------
export function getSubtreeWidth(node) {
  if (!node || !node.children || node.children.length === 0) return NODE_W;
  const childrenWidth = node.children.reduce((sum, c) => sum + getSubtreeWidth(c), 0);
  const totalGaps = (node.children.length - 1) * H_GAP;
  return Math.max(NODE_W, childrenWidth + totalGaps);
}

export function getSubtreeHeight(node) {
  if (!node || !node.children || node.children.length === 0) return NODE_H;
  const maxChild = node.children.reduce((m, c) => Math.max(m, getSubtreeHeight(c)), 0);
  return NODE_H + V_GAP + maxChild;
}

export function layoutNode(node, x, y, positions = {}) {
  positions[node.id] = { x, y };
  if (!node.children || node.children.length === 0) return positions;
  const totalWidth = getSubtreeWidth(node);
  let childX = x - totalWidth / 2;
  for (const child of node.children) {
    const cw = getSubtreeWidth(child);
    layoutNode(child, childX + cw / 2, y + NODE_H + V_GAP, positions);
    childX += cw + H_GAP;
  }
  return positions;
}

export function computeBounds(positions) {
  const xs = Object.values(positions).map((p) => p.x);
  const ys = Object.values(positions).map((p) => p.y);
  if (xs.length === 0) return { minX: 0, minY: 0, width: NODE_W, height: NODE_H };
  return {
    minX: Math.min(...xs) - NODE_W / 2 - 12,
    minY: Math.min(...ys) - 12,
    width: Math.max(...xs) - Math.min(...xs) + NODE_W + 24,
    height: Math.max(...ys) - Math.min(...ys) + NODE_H + 24,
  };
}

// ---------------------------------------------------------------------------
// Collapse handling — mirrors the interactive getVisibleNodes discipline: a
// collapsed node stays visible but its whole subtree is hidden. "Expand all"
// ignores the collapsed set entirely and yields the full tree.
// ---------------------------------------------------------------------------
export function buildVisibleTree(node, collapsed, expandAll) {
  if (!node) return null;
  const collapsedSet = collapsed instanceof Set ? collapsed : new Set(collapsed || []);
  const showChildren = expandAll || !collapsedSet.has(node.id);
  const children = showChildren
    ? (node.children || []).map((c) => buildVisibleTree(c, collapsedSet, expandAll))
    : [];
  return { ...node, children };
}

export function collectIds(node, acc = new Set()) {
  if (!node) return acc;
  acc.add(node.id);
  (node.children || []).forEach((c) => collectIds(c, acc));
  return acc;
}

// ---------------------------------------------------------------------------
// Pagination — one subtree per page, split (never shrunk) until every page's
// laid-out subtree fits the printable box. A too-large node becomes a depth-2
// "overview" page (the node + its direct children as leaves), and each of its
// children that itself has children recurses into its own detail page(s). This
// guarantees full coverage (every visible node appears on some page) with no
// silent truncation and no sub-scale-1 shrinking.
// ---------------------------------------------------------------------------
function fitsPage(node) {
  return getSubtreeWidth(node) <= MAX_PAGE_W && getSubtreeHeight(node) <= PAGE_TREE_H;
}

function asLeaf(node) {
  return { ...node, children: [] };
}

// Split a node's direct children into width-fitting overview chunks (used when
// a node has too many direct children to show on one page even as leaves).
function chunkOverview(node) {
  const leafChildren = (node.children || []).map(asLeaf);
  const groups = [];
  let cur = [];
  let curW = 0;
  for (const child of leafChildren) {
    const add = (cur.length ? H_GAP : 0) + NODE_W;
    if (cur.length && curW + add > MAX_PAGE_W) {
      groups.push(cur);
      cur = [];
      curW = 0;
    }
    cur.push(child);
    curW += (cur.length > 1 ? H_GAP : 0) + NODE_W;
  }
  if (cur.length) groups.push(cur);
  if (groups.length === 0) return [{ kind: "overview", root: asLeaf(node) }];
  return groups.map((g) => ({ kind: "overview", root: { ...node, children: g } }));
}

export function splitToPages(node) {
  if (!node) return [];
  if (fitsPage(node)) {
    return [{ kind: "subtree", root: node }];
  }
  const pages = chunkOverview(node);
  for (const child of node.children || []) {
    if (child.children && child.children.length > 0) {
      pages.push(...splitToPages(child));
    }
  }
  return pages;
}

// ---------------------------------------------------------------------------
// The public entry point: turn the interactive view's current (already
// visibility-filtered) tree + its collapsed state into a print model.
// ---------------------------------------------------------------------------
export function buildExportModel(
  tree,
  { collapsed, expandAll = false, focalName, asOf, today, generatedAt } = {},
) {
  const asOfLabel = asOf || today || "";
  const header = {
    focalName: focalName ?? tree?.display_name ?? "",
    asOfLabel,
    asOf: asOf || null,
    isHistorical: !!(asOf && today && asOf < today),
    generatedAt: generatedAt || "",
  };
  const legend = [
    { key: "ownership", label: "Ownership (%)" },
    { key: "beneficiary", label: "Beneficiary" },
  ];

  if (!tree) {
    return { header, legend, pages: [], pageCount: 0, totalNodes: 0, nodeIds: [] };
  }

  const visible = buildVisibleTree(tree, collapsed, expandAll);
  const pages = splitToPages(visible).map((p, i) => ({
    ...p,
    index: i + 1,
    width: getSubtreeWidth(p.root),
    height: getSubtreeHeight(p.root),
  }));

  const nodeIds = [...collectIds(visible)];
  return {
    header,
    legend,
    pages,
    pageCount: pages.length,
    totalNodes: nodeIds.length,
    nodeIds,
    expandAll: !!expandAll,
  };
}

// Union of every id that actually appears on a rendered page (overview leaves
// included). Used by verification to prove nothing is silently dropped.
export function allPageNodeIds(model) {
  const acc = new Set();
  for (const page of model.pages || []) collectIds(page.root, acc);
  return [...acc];
}
