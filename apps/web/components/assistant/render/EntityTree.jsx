"use client";

const TYPE_LABELS = {
  individual: "Individual",
  entity: "Entity",
  trust: "Trust",
  llc: "LLC",
  lp: "LP",
  household: "Household",
};

function TypeBadge({ type }) {
  return (
    <span
      style={{
        display: "inline-block",
        padding: "1px 7px",
        background: "#F5F1EB",
        border: "1px solid #ece8dd",
        borderRadius: 3,
        fontSize: 11,
        fontWeight: 600,
        textTransform: "uppercase",
        letterSpacing: "0.18em",
        color: "#64748B",
        whiteSpace: "nowrap",
      }}
    >
      {TYPE_LABELS[type] ?? type}
    </span>
  );
}

function fmtPct(pct) {
  if (pct == null) return null;
  const n = parseFloat(pct);
  if (isNaN(n)) return null;
  return `${n.toFixed(1)}%`;
}

function TreeNode({ node, depth = 0 }) {
  const indent = depth * 20;
  const pct = fmtPct(node.ownership_pct);

  return (
    <>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "7px 0",
          paddingLeft: indent,
          borderBottom: "1px solid #f3efe8",
        }}
      >
        {depth > 0 && (
          <span
            style={{
              display: "inline-block",
              width: 14,
              height: 14,
              flexShrink: 0,
              position: "relative",
              top: 1,
            }}
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 14 14"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
            >
              <path
                d="M2 2 L2 9 L12 9"
                stroke="#C5A880"
                strokeWidth="1.4"
                strokeLinecap="round"
                strokeLinejoin="round"
                fill="none"
              />
            </svg>
          </span>
        )}

        <span
          style={{
            flex: 1,
            fontSize: depth === 0 ? 15 : 14,
            fontWeight: depth === 0 ? 600 : 400,
            color: "#0F172A",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {node.name ?? node.display_name ?? node.id}
        </span>

        <TypeBadge type={node.entity_type ?? node.type} />

        {pct && (
          <span
            style={{
              fontSize: 12,
              fontWeight: 500,
              color: "#334155",
              fontVariantNumeric: "tabular-nums",
              flexShrink: 0,
              marginLeft: 4,
            }}
          >
            {pct}
          </span>
        )}
      </div>

      {depth < 2 &&
        Array.isArray(node.children) &&
        node.children.map((child, i) => (
          <TreeNode key={child.id ?? i} node={child} depth={depth + 1} />
        ))}
    </>
  );
}

export default function EntityTree({ entity_id, tree, lookthrough }) {
  const isEmpty =
    !tree ||
    (Array.isArray(tree) ? tree.length === 0 : !tree.id && !tree.name);

  return (
    <div
      style={{
        background: "#FFFFFF",
        border: "1px solid #ece8dd",
        borderRadius: 6,
        padding: "16px 20px",
        fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 12,
        }}
      >
        <span
          style={{
            fontSize: 11,
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: "0.22em",
            color: "#C5A880",
          }}
        >
          Ownership Structure
        </span>

        {lookthrough && (
          <span
            style={{
              fontSize: 11,
              fontWeight: 600,
              textTransform: "uppercase",
              letterSpacing: "0.18em",
              color: "#64748B",
            }}
          >
            Look-through
          </span>
        )}
      </div>

      {isEmpty ? (
        <p
          style={{
            margin: 0,
            fontSize: 14,
            color: "#64748B",
            fontStyle: "italic",
          }}
        >
          No ownership structure found.
        </p>
      ) : (
        <div
          style={{
            maxHeight: 320,
            overflowY: "auto",
            overflowX: "hidden",
          }}
        >
          {Array.isArray(tree) ? (
            tree.map((node, i) => (
              <TreeNode key={node.id ?? i} node={node} depth={0} />
            ))
          ) : (
            <TreeNode node={tree} depth={0} />
          )}
        </div>
      )}

      <div
        style={{
          borderTop: "1px solid #ece8dd",
          paddingTop: 12,
          marginTop: 12,
        }}
      >
        <a
          href={`/crm/${entity_id}/hierarchy`}
          style={{
            fontSize: 13,
            fontWeight: 500,
            color: "#C5A880",
            textDecoration: "none",
            letterSpacing: "0.01em",
          }}
          onMouseEnter={(e) => (e.currentTarget.style.textDecoration = "underline")}
          onMouseLeave={(e) => (e.currentTarget.style.textDecoration = "none")}
        >
          View full hierarchy →
        </a>
      </div>
    </div>
  );
}
