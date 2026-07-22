"use client";

import { useState, useRef, useEffect, useCallback } from "react";

const ENTITY_COLORS = {
  individual: "#EEF4FF",
  trust: "#F0FDF4",
  llc: "#FFF7ED",
  household: "var(--2a-bg)",
  spv: "var(--2a-bg-sidebar)",
  foundation: "#FEF3F2",
  default: "#F8FAFC",
};

const NODE_W = 160;
const NODE_H = 52;
const H_GAP = 32;
const V_GAP = 80;

function getSubtreeWidth(node, collapsed) {
  if (!node.children || node.children.length === 0 || collapsed.has(node.id)) {
    return NODE_W;
  }
  const childrenWidth = node.children.reduce((sum, child) => {
    return sum + getSubtreeWidth(child, collapsed);
  }, 0);
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
  const minX = Math.min(...xs) - NODE_W / 2 - 40;
  const minY = Math.min(...ys) - 40;
  const maxX = Math.max(...xs) + NODE_W / 2 + 40;
  const maxY = Math.max(...ys) + NODE_H + 40;
  return { minX, minY, width: maxX - minX, height: maxY - minY };
}

function collectAllNodes(node, result = []) {
  result.push(node);
  if (node.children) {
    for (const child of node.children) {
      collectAllNodes(child, result);
    }
  }
  return result;
}

function collectEdges(node, result = []) {
  if (node.children) {
    for (const child of node.children) {
      result.push({ parent: node, child });
      collectEdges(child, result);
    }
  }
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

function getVisibleNodes(node, collapsed, result = []) {
  result.push(node);
  if (!node.children || collapsed.has(node.id)) return result;
  for (const child of node.children) {
    getVisibleNodes(child, collapsed, result);
  }
  return result;
}

function findNodeById(tree, id) {
  if (!tree) return null;
  if (tree.id === id) return tree;
  if (tree.children) {
    for (const child of tree.children) {
      const found = findNodeById(child, id);
      if (found) return found;
    }
  }
  return null;
}

function findParents(tree, targetId, parents = [], parentNode = null) {
  if (!tree) return parents;
  if (tree.children) {
    for (const child of tree.children) {
      if (child.id === targetId) {
        if (parentNode) parents.push({ node: parentNode, pct: child.ownership_pct });
      } else {
        findParents(child, targetId, parents, tree);
      }
    }
  }
  return parents;
}

function collectDescendants(node, result = []) {
  if (!node.children) return result;
  for (const child of node.children) {
    result.push(child);
    collectDescendants(child, result);
  }
  return result;
}

function TreeNode({ node, position, isSelected, isCollapsed, onSelect, onToggle, lookthroughMap, showLookthrough }) {
  const bgColor = ENTITY_COLORS[node.entity_type] || ENTITY_COLORS.default;
  const borderColor = isSelected ? "var(--2a-gold)" : "#ece8dd";
  const borderWidth = isSelected ? 2 : 1;
  const hasChildren = node.children && node.children.length > 0;
  const effectivePct = lookthroughMap && lookthroughMap[node.id];

  return (
    <g
      transform={`translate(${position.x - NODE_W / 2}, ${position.y})`}
      style={{ cursor: "pointer" }}
      onClick={(e) => {
        e.stopPropagation();
        onSelect(node.id);
      }}
    >
      <rect
        width={NODE_W}
        height={NODE_H}
        rx={6}
        ry={6}
        fill={bgColor}
        stroke={borderColor}
        strokeWidth={borderWidth}
      />
      <text
        x={NODE_W / 2}
        y={18}
        textAnchor="middle"
        fontSize={13}
        fontWeight="600"
        fontFamily="'Hanken Grotesk', system-ui, sans-serif"
        fill="var(--2a-text)"
        clipPath={`url(#clip-${node.id})`}
      >
        {node.display_name}
      </text>
      <text
        x={NODE_W / 2}
        y={34}
        textAnchor="middle"
        fontSize={11}
        fontFamily="'Hanken Grotesk', system-ui, sans-serif"
        fill="var(--2a-gold)"
        fontWeight="500"
        style={{ textTransform: "uppercase", letterSpacing: "0.05em" }}
      >
        {node.entity_type}
      </text>
      {showLookthrough && effectivePct != null && (
        <text
          x={NODE_W / 2}
          y={46}
          textAnchor="middle"
          fontSize={10}
          fontFamily="'Hanken Grotesk', system-ui, sans-serif"
          fill="var(--2a-text-muted)"
        >
          {Number(effectivePct).toFixed(1)}% eff.
        </text>
      )}
      {hasChildren && (
        <g
          transform={`translate(${NODE_W - 16}, ${NODE_H / 2 - 6})`}
          onClick={(e) => {
            e.stopPropagation();
            onToggle(node.id);
          }}
          style={{ cursor: "pointer" }}
        >
          <rect width={12} height={12} rx={3} fill="var(--2a-navy)" opacity={0.1} />
          <text
            x={6}
            y={9}
            textAnchor="middle"
            fontSize={10}
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
          <rect x={8} y={0} width={NODE_W - 24} height={NODE_H} />
        </clipPath>
      </defs>
    </g>
  );
}

function EdgePath({ parent, child, parentPos, childPos }) {
  if (!parentPos || !childPos) return null;

  const x1 = parentPos.x;
  const y1 = parentPos.y + NODE_H;
  const x2 = childPos.x;
  const y2 = childPos.y;
  const midY = (y1 + y2) / 2;

  const d = `M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`;
  const labelX = (x1 + x2) / 2;
  const labelY = midY;

  const pct = child.ownership_pct != null ? `${Number(child.ownership_pct).toFixed(1)}%` : null;

  return (
    <g>
      <path d={d} fill="none" stroke="var(--2a-border)" strokeWidth={1.5} />
      {pct && (
        <>
          <rect
            x={labelX - 16}
            y={labelY - 9}
            width={32}
            height={14}
            rx={3}
            fill="white"
            stroke="#ece8dd"
            strokeWidth={1}
          />
          <text
            x={labelX}
            y={labelY + 1}
            textAnchor="middle"
            fontSize={11}
            fontFamily="'Hanken Grotesk', system-ui, sans-serif"
            fill="var(--2a-gold)"
            fontWeight="500"
          >
            {pct}
          </text>
        </>
      )}
    </g>
  );
}

function TypeBadge({ type }) {
  const bg = ENTITY_COLORS[type] || ENTITY_COLORS.default;
  return (
    <span
      style={{
        background: bg,
        border: "1px solid #ece8dd",
        borderRadius: 4,
        padding: "2px 8px",
        fontSize: 11,
        fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
        color: "var(--2a-gold)",
        fontWeight: 600,
        letterSpacing: "0.05em",
        textTransform: "uppercase",
      }}
    >
      {type}
    </span>
  );
}

function SideRail({ tree, selected, lookthroughMap, showLookthrough, onShowLookthroughChange, staff, onRefresh }) {
  const [addForm, setAddForm] = useState({ fromId: "", toId: "", pct: "" });
  const [addError, setAddError] = useState(null);
  const [addLoading, setAddLoading] = useState(false);
  const [editId, setEditId] = useState(null);
  const [editPct, setEditPct] = useState("");
  const [editLoading, setEditLoading] = useState(false);
  const [deleteLoading, setDeleteLoading] = useState(null);

  const selectedNode = selected ? findNodeById(tree, selected) : null;
  const parents = selected && tree ? findParents(tree, selected, [], null) : [];
  const children = selectedNode?.children || [];
  const descendants = selectedNode ? collectDescendants(selectedNode) : [];

  const handleAddRelationship = async (e) => {
    e.preventDefault();
    setAddError(null);
    setAddLoading(true);
    try {
      const res = await fetch("/api/entity-relationships", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          from_entity_id: addForm.fromId,
          to_entity_id: addForm.toId,
          ownership_pct: addForm.pct ? Number(addForm.pct) : null,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        if (data?.detail?.includes?.("cycle") || data?.detail?.includes?.("Cycle")) {
          setAddError("Cycle detected: this relationship would create a circular ownership chain.");
        } else {
          setAddError(data?.detail || "Failed to add relationship.");
        }
      } else {
        setAddForm({ fromId: "", toId: "", pct: "" });
        if (onRefresh) onRefresh();
      }
    } catch (err) {
      setAddError("Network error. Please try again.");
    } finally {
      setAddLoading(false);
    }
  };

  const handleDelete = async (relId) => {
    setDeleteLoading(relId);
    try {
      const res = await fetch(`/api/entity-relationships/${relId}`, { method: "DELETE" });
      if (res.ok && onRefresh) onRefresh();
    } finally {
      setDeleteLoading(null);
    }
  };

  const handleEditSubmit = async (relId) => {
    setEditLoading(true);
    try {
      const res = await fetch(`/api/entity-relationships/${relId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ownership_pct: editPct ? Number(editPct) : null }),
      });
      if (res.ok) {
        setEditId(null);
        setEditPct("");
        if (onRefresh) onRefresh();
      }
    } finally {
      setEditLoading(false);
    }
  };

  const labelStyle = {
    fontSize: 11,
    fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
    fontWeight: 600,
    color: "var(--2a-text-muted)",
    textTransform: "uppercase",
    letterSpacing: "0.12em",
    display: "block",
    marginBottom: 4,
  };

  const inputStyle = {
    width: "100%",
    padding: "7px 10px",
    fontSize: 13,
    fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
    border: "1px solid var(--2a-border)",
    borderRadius: 5,
    color: "var(--2a-text)",
    background: "#fff",
    outline: "none",
    boxSizing: "border-box",
  };

  const sectionHead = {
    fontSize: 11,
    fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
    fontWeight: 700,
    color: "var(--2a-text-muted)",
    textTransform: "uppercase",
    letterSpacing: "0.12em",
    marginBottom: 8,
    marginTop: 16,
  };

  return (
    <div
      style={{
        width: 280,
        minWidth: 280,
        borderLeft: "1px solid #ece8dd",
        background: "#fff",
        padding: "20px 18px",
        overflowY: "auto",
        flexShrink: 0,
      }}
    >
      {/* Look-through toggle */}
      <div style={{ marginBottom: 16, display: "flex", alignItems: "center", gap: 8 }}>
        <button
          onClick={() => onShowLookthroughChange(!showLookthrough)}
          style={{
            background: showLookthrough ? "var(--2a-navy)" : "var(--2a-bg-sidebar)",
            color: showLookthrough ? "var(--2a-gold-light)" : "var(--2a-navy)",
            border: "1px solid #ece8dd",
            borderRadius: 5,
            padding: "5px 12px",
            fontSize: 12,
            fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
            fontWeight: 600,
            cursor: "pointer",
            letterSpacing: "0.04em",
          }}
        >
          {showLookthrough ? "Look-through ON" : "Look-through OFF"}
        </button>
      </div>

      {!selectedNode ? (
        <div style={{ color: "var(--2a-text-muted)", fontSize: 13, fontFamily: "'Hanken Grotesk', system-ui, sans-serif" }}>
          Select a node to view details.
        </div>
      ) : (
        <>
          <div style={{ marginBottom: 12 }}>
            <div
              style={{
                fontFamily: "'Spectral', Georgia, serif",
                fontSize: 17,
                fontWeight: 500,
                color: "var(--2a-text)",
                marginBottom: 6,
              }}
            >
              {selectedNode.display_name}
            </div>
            <TypeBadge type={selectedNode.entity_type} />
          </div>

          {selectedNode.ownership_pct != null && (
            <div style={{ fontSize: 13, fontFamily: "'Hanken Grotesk', system-ui, sans-serif", color: "var(--2a-text-secondary)", marginBottom: 8 }}>
              Ownership from parent:{" "}
              <span style={{ fontWeight: 600, color: "var(--2a-gold)" }}>
                {Number(selectedNode.ownership_pct).toFixed(2)}%
              </span>
            </div>
          )}

          {showLookthrough && lookthroughMap && lookthroughMap[selectedNode.id] != null && (
            <div style={{ fontSize: 13, fontFamily: "'Hanken Grotesk', system-ui, sans-serif", color: "var(--2a-text-secondary)", marginBottom: 8 }}>
              Effective ownership:{" "}
              <span style={{ fontWeight: 600, color: "var(--2a-gold)" }}>
                {Number(lookthroughMap[selectedNode.id]).toFixed(4)}%
              </span>
            </div>
          )}

          {parents.length > 0 && (
            <>
              <div style={sectionHead}>Parents</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {parents.map(({ node: pNode, pct }, i) => (
                  <div
                    key={i}
                    style={{
                      fontSize: 12,
                      fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
                      color: "var(--2a-text-secondary)",
                      background: "var(--2a-bg)",
                      border: "1px solid #ece8dd",
                      borderRadius: 5,
                      padding: "5px 10px",
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                    }}
                  >
                    <span>{pNode.display_name}</span>
                    {pct != null && (
                      <span style={{ color: "var(--2a-gold)", fontWeight: 600 }}>{Number(pct).toFixed(1)}%</span>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}

          {children.length > 0 && (
            <>
              <div style={sectionHead}>Children</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {children.map((child) => (
                  <div
                    key={child.id}
                    style={{
                      fontSize: 12,
                      fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
                      color: "var(--2a-text-secondary)",
                      background: "var(--2a-bg)",
                      border: "1px solid #ece8dd",
                      borderRadius: 5,
                      padding: "5px 10px",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <span>{child.display_name}</span>
                      {child.ownership_pct != null && (
                        <span style={{ color: "var(--2a-gold)", fontWeight: 600 }}>
                          {Number(child.ownership_pct).toFixed(1)}%
                        </span>
                      )}
                    </div>
                    {staff && child.relationship_id && (
                      <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
                        {editId === child.relationship_id ? (
                          <>
                            <input
                              type="number"
                              min={0}
                              max={100}
                              step={0.01}
                              value={editPct}
                              onChange={(e) => setEditPct(e.target.value)}
                              style={{ ...inputStyle, width: 70, padding: "3px 6px" }}
                              placeholder="% "
                            />
                            <button
                              onClick={() => handleEditSubmit(child.relationship_id)}
                              disabled={editLoading}
                              style={{
                                background: "var(--2a-navy)",
                                color: "var(--2a-gold-light)",
                                border: "none",
                                borderRadius: 4,
                                padding: "3px 8px",
                                fontSize: 11,
                                fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
                                cursor: "pointer",
                              }}
                            >
                              Save
                            </button>
                            <button
                              onClick={() => { setEditId(null); setEditPct(""); }}
                              style={{
                                background: "var(--2a-bg-sidebar)",
                                color: "var(--2a-text-secondary)",
                                border: "1px solid #ece8dd",
                                borderRadius: 4,
                                padding: "3px 8px",
                                fontSize: 11,
                                fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
                                cursor: "pointer",
                              }}
                            >
                              Cancel
                            </button>
                          </>
                        ) : (
                          <>
                            <button
                              onClick={() => { setEditId(child.relationship_id); setEditPct(child.ownership_pct ?? ""); }}
                              style={{
                                background: "var(--2a-bg-sidebar)",
                                color: "var(--2a-text-secondary)",
                                border: "1px solid #ece8dd",
                                borderRadius: 4,
                                padding: "3px 8px",
                                fontSize: 11,
                                fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
                                cursor: "pointer",
                              }}
                            >
                              Edit %
                            </button>
                            <button
                              onClick={() => handleDelete(child.relationship_id)}
                              disabled={deleteLoading === child.relationship_id}
                              style={{
                                background: "#FEF3F2",
                                color: "#9B2335",
                                border: "1px solid #f5c6cb",
                                borderRadius: 4,
                                padding: "3px 8px",
                                fontSize: 11,
                                fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
                                cursor: "pointer",
                              }}
                            >
                              {deleteLoading === child.relationship_id ? "..." : "Remove"}
                            </button>
                          </>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}

          {showLookthrough && lookthroughMap && descendants.length > 0 && (
            <>
              <div style={sectionHead}>Descendants (effective %)</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                {descendants.map((desc) =>
                  lookthroughMap[desc.id] != null ? (
                    <div
                      key={desc.id}
                      style={{
                        fontSize: 12,
                        fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
                        color: "var(--2a-text-secondary)",
                        display: "flex",
                        justifyContent: "space-between",
                        padding: "3px 8px",
                        background: "var(--2a-bg)",
                        border: "1px solid #ece8dd",
                        borderRadius: 4,
                      }}
                    >
                      <span>{desc.display_name}</span>
                      <span style={{ color: "var(--2a-gold)", fontWeight: 600 }}>
                        {Number(lookthroughMap[desc.id]).toFixed(4)}%
                      </span>
                    </div>
                  ) : null
                )}
              </div>
            </>
          )}
        </>
      )}

      {staff && (
        <>
          <div
            style={{
              marginTop: 24,
              paddingTop: 16,
              borderTop: "1px solid #ece8dd",
            }}
          >
            <div
              style={{
                fontFamily: "'Spectral', Georgia, serif",
                fontSize: 15,
                fontWeight: 500,
                color: "var(--2a-navy)",
                marginBottom: 12,
              }}
            >
              Add Relationship
            </div>
            <form onSubmit={handleAddRelationship} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <div>
                <label style={labelStyle}>From Entity ID</label>
                <input
                  type="text"
                  value={addForm.fromId}
                  onChange={(e) => setAddForm((f) => ({ ...f, fromId: e.target.value }))}
                  style={inputStyle}
                  placeholder="UUID"
                  required
                />
              </div>
              <div>
                <label style={labelStyle}>To Entity ID</label>
                <input
                  type="text"
                  value={addForm.toId}
                  onChange={(e) => setAddForm((f) => ({ ...f, toId: e.target.value }))}
                  style={inputStyle}
                  placeholder="UUID"
                  required
                />
              </div>
              <div>
                <label style={labelStyle}>Ownership %</label>
                <input
                  type="number"
                  min={0}
                  max={100}
                  step={0.01}
                  value={addForm.pct}
                  onChange={(e) => setAddForm((f) => ({ ...f, pct: e.target.value }))}
                  style={inputStyle}
                  placeholder="0 – 100"
                />
              </div>
              {addError && (
                <div
                  style={{
                    fontSize: 12,
                    fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
                    color: "#9B2335",
                    background: "#FEF3F2",
                    border: "1px solid #f5c6cb",
                    borderRadius: 5,
                    padding: "7px 10px",
                  }}
                >
                  {addError}
                </div>
              )}
              <button
                type="submit"
                disabled={addLoading}
                style={{
                  background: "var(--2a-navy)",
                  color: "var(--2a-gold-light)",
                  border: "none",
                  borderRadius: 5,
                  padding: "8px 0",
                  fontSize: 13,
                  fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
                  fontWeight: 600,
                  cursor: addLoading ? "not-allowed" : "pointer",
                  opacity: addLoading ? 0.7 : 1,
                  letterSpacing: "0.04em",
                }}
              >
                {addLoading ? "Adding..." : "Add Relationship"}
              </button>
            </form>
          </div>
        </>
      )}
    </div>
  );
}

function EntityGroupPanel({ staff }) {
  const [groups, setGroups] = useState([]);
  const [loading, setLoading] = useState(true);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState(null);

  const fetchGroups = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/entity-groups");
      if (res.ok) {
        const data = await res.json();
        setGroups(data.groups || data || []);
      }
    } catch {
      // silently fail
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchGroups();
  }, [fetchGroups]);

  const handleCreate = async (e) => {
    e.preventDefault();
    setError(null);
    setCreating(true);
    try {
      const res = await fetch("/api/entity-groups", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newName, description: newDesc }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data?.detail || "Failed to create group.");
      } else {
        setNewName("");
        setNewDesc("");
        fetchGroups();
      }
    } catch {
      setError("Network error. Please try again.");
    } finally {
      setCreating(false);
    }
  };

  const inputStyle = {
    padding: "7px 10px",
    fontSize: 13,
    fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
    border: "1px solid var(--2a-border)",
    borderRadius: 5,
    color: "var(--2a-text)",
    background: "#fff",
    outline: "none",
    boxSizing: "border-box",
  };

  return (
    <div
      style={{
        borderTop: "1px solid #ece8dd",
        background: "var(--2a-bg)",
        padding: "20px 24px",
      }}
    >
      <div
        style={{
          fontFamily: "'Spectral', Georgia, serif",
          fontSize: 17,
          fontWeight: 500,
          color: "var(--2a-navy)",
          marginBottom: 14,
        }}
      >
        Entity Groups
      </div>

      {loading ? (
        <div style={{ fontSize: 13, color: "var(--2a-text-muted)", fontFamily: "'Hanken Grotesk', system-ui, sans-serif" }}>
          Loading groups...
        </div>
      ) : groups.length === 0 ? (
        <div style={{ fontSize: 13, color: "var(--2a-text-muted)", fontFamily: "'Hanken Grotesk', system-ui, sans-serif", marginBottom: 12 }}>
          No groups yet.
        </div>
      ) : (
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: 10,
            marginBottom: 16,
          }}
        >
          {groups.map((group) => (
            <div
              key={group.id}
              style={{
                background: "#fff",
                border: "1px solid #ece8dd",
                borderRadius: 6,
                padding: "8px 14px",
                fontSize: 13,
                fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
                color: "var(--2a-text)",
                boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
              }}
            >
              <div style={{ fontWeight: 600 }}>{group.name}</div>
              {group.description && (
                <div style={{ fontSize: 12, color: "var(--2a-text-muted)", marginTop: 2 }}>{group.description}</div>
              )}
              <div style={{ fontSize: 11, color: "var(--2a-gold)", marginTop: 4, fontWeight: 500 }}>
                {group.member_count ?? (group.members?.length ?? 0)} member{(group.member_count ?? (group.members?.length ?? 0)) !== 1 ? "s" : ""}
              </div>
            </div>
          ))}
        </div>
      )}

      {staff && (
        <form onSubmit={handleCreate} style={{ display: "flex", alignItems: "flex-end", gap: 8, flexWrap: "wrap" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <label
              style={{
                fontSize: 11,
                fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
                fontWeight: 600,
                color: "var(--2a-text-muted)",
                textTransform: "uppercase",
                letterSpacing: "0.12em",
              }}
            >
              New Group Name
            </label>
            <input
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              style={{ ...inputStyle, width: 200 }}
              placeholder="Group name"
              required
            />
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <label
              style={{
                fontSize: 11,
                fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
                fontWeight: 600,
                color: "var(--2a-text-muted)",
                textTransform: "uppercase",
                letterSpacing: "0.12em",
              }}
            >
              Description
            </label>
            <input
              type="text"
              value={newDesc}
              onChange={(e) => setNewDesc(e.target.value)}
              style={{ ...inputStyle, width: 240 }}
              placeholder="Optional description"
            />
          </div>
          <button
            type="submit"
            disabled={creating}
            style={{
              background: "var(--2a-navy)",
              color: "var(--2a-gold-light)",
              border: "none",
              borderRadius: 5,
              padding: "8px 16px",
              fontSize: 13,
              fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
              fontWeight: 600,
              cursor: creating ? "not-allowed" : "pointer",
              opacity: creating ? 0.7 : 1,
              letterSpacing: "0.04em",
              height: 36,
            }}
          >
            {creating ? "Creating..." : "Create Group"}
          </button>
          {error && (
            <div
              style={{
                fontSize: 12,
                fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
                color: "#9B2335",
                alignSelf: "center",
              }}
            >
              {error}
            </div>
          )}
        </form>
      )}
    </div>
  );
}

export default function HierarchyBuilder({ entityId, tree, lookthrough, staff }) {
  const [collapsed, setCollapsed] = useState(new Set());
  const [selected, setSelected] = useState(null);
  const [showLookthrough, setShowLookthrough] = useState(false);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(1);
  const [treeData, setTreeData] = useState(tree);
  const [lookthroughData, setLookthroughData] = useState(lookthrough);

  const isDragging = useRef(false);
  const dragStart = useRef({ x: 0, y: 0 });
  const panStart = useRef({ x: 0, y: 0 });
  const svgContainerRef = useRef(null);

  // Build lookthrough map: entity_id -> effective_pct
  const lookthroughMap = useCallback(() => {
    if (!lookthroughData) return {};
    const map = {};
    for (const item of lookthroughData) {
      if (item.entity_id) map[item.entity_id] = item.effective_pct ?? item.ownership_pct;
    }
    return map;
  }, [lookthroughData])();

  const handleRefresh = useCallback(async () => {
    if (!entityId) return;
    try {
      const [treeRes, ltRes] = await Promise.all([
        fetch(`/api/entities/${entityId}/tree`),
        fetch(`/api/entities/${entityId}/lookthrough`),
      ]);
      if (treeRes.ok) {
        const data = await treeRes.json();
        setTreeData(data);
      }
      if (ltRes.ok) {
        const data = await ltRes.json();
        setLookthroughData(data.lookthrough || []);
      }
    } catch {
      // silently fail
    }
  }, [entityId]);

  const toggleCollapse = useCallback((nodeId) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(nodeId)) {
        next.delete(nodeId);
      } else {
        next.add(nodeId);
      }
      return next;
    });
  }, []);

  const handleSelect = useCallback((nodeId) => {
    setSelected((prev) => (prev === nodeId ? null : nodeId));
  }, []);

  // Mouse events for pan
  const handleMouseDown = useCallback((e) => {
    if (e.button !== 0) return;
    // Only pan if clicking on the SVG background (not a node)
    if (e.target.tagName === "svg" || e.target.tagName === "SVG") {
      isDragging.current = true;
      dragStart.current = { x: e.clientX, y: e.clientY };
      panStart.current = { ...pan };
      e.preventDefault();
    }
  }, [pan]);

  const handleMouseMove = useCallback((e) => {
    if (!isDragging.current) return;
    const dx = e.clientX - dragStart.current.x;
    const dy = e.clientY - dragStart.current.y;
    setPan({ x: panStart.current.x + dx, y: panStart.current.y + dy });
  }, []);

  const handleMouseUp = useCallback(() => {
    isDragging.current = false;
  }, []);

  const handleWheel = useCallback((e) => {
    e.preventDefault();
    const delta = e.deltaY > 0 ? -0.1 : 0.1;
    setZoom((prev) => Math.min(3, Math.max(0.3, prev + delta)));
  }, []);

  useEffect(() => {
    const el = svgContainerRef.current;
    if (!el) return;
    el.addEventListener("wheel", handleWheel, { passive: false });
    return () => el.removeEventListener("wheel", handleWheel);
  }, [handleWheel]);

  if (!treeData) {
    return (
      <div
        style={{
          background: "#fff",
          border: "1px solid #ece8dd",
          borderRadius: 8,
          padding: 32,
          fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
          fontSize: 14,
          color: "var(--2a-text-muted)",
        }}
      >
        No ownership tree data available for this entity.
      </div>
    );
  }

  const positions = layoutNode(treeData, 0, 0, collapsed);
  const bounds = computeSVGBounds(positions);
  const visibleNodes = getVisibleNodes(treeData, collapsed);
  const visibleEdges = getVisibleEdges(treeData, collapsed);

  // Center tree initially
  const svgWidth = bounds.width;
  const svgHeight = bounds.height;

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
      {/* Main area: tree + side rail */}
      <div style={{ display: "flex", flex: 1, minHeight: 480 }}>
        {/* Tree panel */}
        <div
          ref={svgContainerRef}
          style={{
            flex: 1,
            overflow: "hidden",
            position: "relative",
            background: "var(--2a-bg)",
            cursor: isDragging.current ? "grabbing" : "grab",
          }}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
        >
          {/* Zoom controls */}
          <div
            style={{
              position: "absolute",
              top: 12,
              right: 12,
              zIndex: 10,
              display: "flex",
              flexDirection: "column",
              gap: 4,
            }}
          >
            <button
              onClick={() => setZoom((z) => Math.min(3, z + 0.15))}
              style={{
                width: 28,
                height: 28,
                background: "#fff",
                border: "1px solid #ece8dd",
                borderRadius: 5,
                fontSize: 16,
                fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
                color: "var(--2a-navy)",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
              }}
            >
              +
            </button>
            <button
              onClick={() => setZoom((z) => Math.max(0.3, z - 0.15))}
              style={{
                width: 28,
                height: 28,
                background: "#fff",
                border: "1px solid #ece8dd",
                borderRadius: 5,
                fontSize: 16,
                fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
                color: "var(--2a-navy)",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
              }}
            >
              −
            </button>
            <button
              onClick={() => { setPan({ x: 0, y: 0 }); setZoom(1); }}
              style={{
                width: 28,
                height: 28,
                background: "#fff",
                border: "1px solid #ece8dd",
                borderRadius: 5,
                fontSize: 10,
                fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
                color: "var(--2a-navy)",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
                fontWeight: 600,
              }}
            >
              ⌂
            </button>
          </div>

          <svg
            width="100%"
            height="100%"
            style={{ display: "block", minHeight: 480 }}
            onClick={() => setSelected(null)}
          >
            <g
              transform={`translate(${pan.x + (svgContainerRef.current?.offsetWidth ?? 600) / 2}, ${pan.y + 40}) scale(${zoom})`}
              style={{ transformOrigin: "0 0" }}
            >
              {/* Render edges first (behind nodes) */}
              {visibleEdges.map(({ parent, child }) => {
                const pp = positions[parent.id];
                const cp = positions[child.id];
                return (
                  <EdgePath
                    key={`${parent.id}-${child.id}`}
                    parent={parent}
                    child={child}
                    parentPos={pp}
                    childPos={cp}
                  />
                );
              })}
              {/* Render nodes */}
              {visibleNodes.map((node) => {
                const pos = positions[node.id];
                if (!pos) return null;
                return (
                  <TreeNode
                    key={node.id}
                    node={node}
                    position={pos}
                    isSelected={selected === node.id}
                    isCollapsed={collapsed.has(node.id)}
                    onSelect={handleSelect}
                    onToggle={toggleCollapse}
                    lookthroughMap={lookthroughMap}
                    showLookthrough={showLookthrough}
                  />
                );
              })}
            </g>
          </svg>
        </div>

        {/* Side rail */}
        <SideRail
          tree={treeData}
          selected={selected}
          lookthroughMap={lookthroughMap}
          showLookthrough={showLookthrough}
          onShowLookthroughChange={setShowLookthrough}
          staff={staff}
          onRefresh={handleRefresh}
        />
      </div>

      {/* Entity Group Panel */}
      <EntityGroupPanel staff={staff} />
    </div>
  );
}
