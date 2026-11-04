"""BUGFIX2 (WF-01) — give a BPMN document a diagram (DI) when it has none.

The natural-language generator (services.workflow_nl_generator, HARD RULE 7)
deliberately tells the model NOT to emit the ``<bpmndi:BPMNDiagram>`` layout
section: it is pure layout, ``derive_steps`` never reads it, and it was about a
third of the output — the reason live generation used to truncate. But the
diagram editor (bpmn-js ``importXML``) cannot draw a process without it and
fails with "no diagram to display", so every freshly generated workflow opened
as "Could not render diagram: no diagram to display" even though its steps were
stored and summarised ("2 steps, 2 require approval").

:func:`ensure_bpmn_di` is the fix: a deterministic left-to-right layout of each
process's flow nodes (columns by breadth-first distance from the start events
along sequence flows, rows by document order), one ``BPMNShape`` per node and
one ``BPMNEdge`` per sequence flow. A document that already carries any shape is
returned unchanged, so an author's own layout is never overwritten. Only layout
elements are added — the process model, its ids and its governance extension
elements are untouched, so the derived steps are identical before and after.
"""
from __future__ import annotations

from collections import deque

from lxml import etree

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
BPMNDI_NS = "http://www.omg.org/spec/BPMN/20100524/DI"
DC_NS = "http://www.omg.org/spec/DD/20100524/DC"
DI_NS = "http://www.omg.org/spec/DD/20100524/DI"

FLOW_NODE_TYPES = {
    "startEvent", "endEvent", "intermediateCatchEvent", "intermediateThrowEvent",
    "task", "userTask", "serviceTask", "manualTask", "scriptTask", "sendTask", "receiveTask",
    "businessRuleTask", "callActivity", "subProcess", "transaction", "adHocSubProcess",
    "exclusiveGateway", "parallelGateway", "inclusiveGateway", "eventBasedGateway", "complexGateway",
}
EVENT_TYPES = {"startEvent", "endEvent", "intermediateCatchEvent", "intermediateThrowEvent", "boundaryEvent"}
GATEWAY_TYPES = {"exclusiveGateway", "parallelGateway", "inclusiveGateway", "eventBasedGateway", "complexGateway"}

COL_WIDTH = 180
ROW_HEIGHT = 130
ORIGIN_X = 100
ORIGIN_Y = 100


class BpmnLayoutError(ValueError):
    """The document could not be parsed as XML."""


def _q(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def _local(el) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def _size(kind: str) -> tuple[int, int]:
    if kind in EVENT_TYPES:
        return 36, 36
    if kind in GATEWAY_TYPES:
        return 50, 50
    return 100, 80


def has_diagram(xml: str) -> bool:
    """True when the document carries at least one BPMNShape (something bpmn-js can draw)."""
    root = _parse(xml)
    return root.find(f".//{_q(BPMNDI_NS, 'BPMNShape')}") is not None


def _parse(xml: str):
    if not isinstance(xml, str) or not xml.strip():
        raise BpmnLayoutError("no BPMN document")
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True, remove_blank_text=False)
        return etree.fromstring(xml.encode("utf-8"), parser)
    except etree.XMLSyntaxError as exc:
        raise BpmnLayoutError(f"BPMN is not well-formed XML: {exc}") from exc


def _layers(nodes: list, flows: list[tuple[str, str, str]]) -> dict[str, int]:
    """Column per node: breadth-first distance from the start events (cycles safe)."""
    ids = [n.get("id") for n in nodes]
    out_edges: dict[str, list[str]] = {i: [] for i in ids}
    indeg: dict[str, int] = {i: 0 for i in ids}
    for _fid, src, tgt in flows:
        if src in out_edges and tgt in indeg:
            out_edges[src].append(tgt)
            indeg[tgt] += 1
    starts = [n.get("id") for n in nodes if _local(n) == "startEvent"] or [i for i in ids if indeg[i] == 0]
    if not starts and ids:
        starts = [ids[0]]
    layer: dict[str, int] = {}
    queue = deque()
    for s in starts:
        layer[s] = 0
        queue.append(s)
    while queue:
        cur = queue.popleft()
        for nxt in out_edges.get(cur, []):
            if nxt not in layer:
                layer[nxt] = layer[cur] + 1
                queue.append(nxt)
    deepest = max(layer.values(), default=-1)
    for i in ids:                       # unreachable nodes go in one column after the rest
        if i not in layer:
            layer[i] = deepest + 1
    return layer


def ensure_bpmn_di(xml: str) -> str:
    """Return ``xml`` with a generated diagram when it has none; unchanged when it already has one."""
    root = _parse(xml)
    if root.find(f".//{_q(BPMNDI_NS, 'BPMNShape')}") is not None:
        return xml
    # An empty BPMNDiagram (no shapes) is as undrawable as none — replace it.
    for old in root.findall(_q(BPMNDI_NS, "BPMNDiagram")):
        root.remove(old)
    processes = root.findall(_q(BPMN_NS, "process"))
    if not processes:
        return xml

    diagram = etree.SubElement(root, _q(BPMNDI_NS, "BPMNDiagram"), nsmap={"bpmndi": BPMNDI_NS, "dc": DC_NS, "di": DI_NS})
    diagram.set("id", "BPMNDiagram_generated")
    y_offset = 0
    for p_index, process in enumerate(processes):
        plane = etree.SubElement(diagram, _q(BPMNDI_NS, "BPMNPlane"))
        plane.set("id", f"BPMNPlane_generated_{p_index}")
        plane.set("bpmnElement", process.get("id") or f"Process_{p_index}")
        nodes = [c for c in process if _local(c) in FLOW_NODE_TYPES and c.get("id")]
        flows = [(f.get("id"), f.get("sourceRef"), f.get("targetRef"))
                 for f in process if _local(f) == "sequenceFlow" and f.get("id")]
        layer = _layers(nodes, flows)
        rows_used: dict[int, int] = {}
        bounds: dict[str, tuple[int, int, int, int]] = {}
        for n in nodes:
            nid = n.get("id")
            col = layer[nid]
            row = rows_used.get(col, 0)
            rows_used[col] = row + 1
            w, h = _size(_local(n))
            cx = ORIGIN_X + col * COL_WIDTH + 50
            cy = ORIGIN_Y + y_offset + row * ROW_HEIGHT + 40
            x, y = cx - w // 2, cy - h // 2
            bounds[nid] = (x, y, w, h)
            shape = etree.SubElement(plane, _q(BPMNDI_NS, "BPMNShape"))
            shape.set("id", f"{nid}_di")
            shape.set("bpmnElement", nid)
            if _local(n) in ("subProcess", "transaction", "adHocSubProcess"):
                shape.set("isExpanded", "false")
            if _local(n) == "exclusiveGateway":
                shape.set("isMarkerVisible", "true")
            b = etree.SubElement(shape, _q(DC_NS, "Bounds"))
            b.set("x", str(x)); b.set("y", str(y)); b.set("width", str(w)); b.set("height", str(h))
        # Boundary events sit on the lower edge of the activity they are attached to.
        for be in (c for c in process if _local(c) == "boundaryEvent" and c.get("id")):
            host = bounds.get(be.get("attachedToRef") or "")
            if host is None:
                continue
            hx, hy, hw, hh = host
            x, y = hx + hw - 18 - 10, hy + hh - 18
            bounds[be.get("id")] = (x, y, 36, 36)
            shape = etree.SubElement(plane, _q(BPMNDI_NS, "BPMNShape"))
            shape.set("id", f"{be.get('id')}_di")
            shape.set("bpmnElement", be.get("id"))
            b = etree.SubElement(shape, _q(DC_NS, "Bounds"))
            b.set("x", str(x)); b.set("y", str(y)); b.set("width", "36"); b.set("height", "36")
        for fid, src, tgt in flows:
            if src not in bounds or tgt not in bounds:
                continue
            sx, sy, sw, sh = bounds[src]
            tx, ty, tw, th = bounds[tgt]
            start = (sx + sw, sy + sh // 2)
            end = (tx, ty + th // 2)
            if tx <= sx:                # a loop back: leave below, come back in from below
                start = (sx + sw // 2, sy + sh)
                end = (tx + tw // 2, ty + th)
                low = max(sy + sh, ty + th) + 30
                points = [start, (start[0], low), (end[0], low), end]
            elif start[1] == end[1]:
                points = [start, end]
            else:
                mid = (start[0] + end[0]) // 2
                points = [start, (mid, start[1]), (mid, end[1]), end]
            edge = etree.SubElement(plane, _q(BPMNDI_NS, "BPMNEdge"))
            edge.set("id", f"{fid}_di")
            edge.set("bpmnElement", fid)
            for px, py in points:
                wp = etree.SubElement(edge, _q(DI_NS, "waypoint"))
                wp.set("x", str(px)); wp.set("y", str(py))
        y_offset += (max(rows_used.values(), default=1) + 1) * ROW_HEIGHT

    out = etree.tostring(root, encoding="unicode")
    if xml.lstrip().startswith("<?xml"):
        out = '<?xml version="1.0" encoding="UTF-8"?>\n' + out
    return out
