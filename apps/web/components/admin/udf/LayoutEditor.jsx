"use client";

// UDFUI1 — one tab's layout: sections (add / reorder / remove — removal refused by the engine while it has items) and
// their items (add a field / reorder / remove). Reordering: drag (@dnd-kit, already used by DataGrid) or the ↑ / ↓
// buttons (the keyboard equivalent). Sections reorder through PUT …/sections/order (the engine's reorder_sections);
// items through PATCH …/items/{id} display_order. Every write control only when permissions.can_write.
import { useEffect, useState } from "react";
import { DndContext, KeyboardSensor, PointerSensor, closestCenter, useSensor, useSensors } from "@dnd-kit/core";
import { SortableContext, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { BusyButton, BusyError, useBusy } from "@/components/ui/Busy";
import { canWrite, itemOrderPatches, label, moveById, moveInList, placeableDefinitions } from "@/lib/udfBuilder.mjs";
import { Notice, Select, Text, btnCls, primaryCls, udfCall } from "./udfParts";

function Sortable({ id, disabled, children }) {
  const { attributes, listeners, setNodeRef, transform, transition } = useSortable({ id, disabled });
  const style = { transform: CSS.Transform.toString(transform), transition };
  return (
    <li ref={setNodeRef} style={style} className="list-none">
      {children({ handle: disabled ? {} : { ...attributes, ...listeners } })}
    </li>
  );
}

function UpDown({ name, index, count, onMove }) {
  return (
    <span className="flex gap-1">
      <button type="button" className={btnCls} aria-label={`Move ${name} up`} disabled={index === 0} onClick={() => onMove(index, -1)}>↑</button>
      <button type="button" className={btnCls} aria-label={`Move ${name} down`} disabled={index === count - 1} onClick={() => onMove(index, 1)}>↓</button>
    </span>
  );
}

function AddItem({ tab, section, layout, definitions, envelope, onDone }) {
  const busy = useBusy();
  const choices = placeableDefinitions(definitions, tab, layout);
  const [form, setForm] = useState({ definition_id: "", column_index: "0", col_span: "1" });
  const [error, setError] = useState(null);
  if (!choices.length) return <Notice>Every active field for this record type is already placed on this tab.</Notice>;
  return (
    <form className="mt-2 grid grid-cols-1 items-end gap-x-3 md:grid-cols-4" aria-label={`Add a field to ${section.title || "section"}`}
      onSubmit={(e) => {
        e.preventDefault();
        if (!form.definition_id) { setError("Choose a field."); return; }
        setError(null);
        busy.run(async () => {
          await udfCall("addItem", { tab: tab.id, section: section.id }, { body: {
            definition_id: form.definition_id, column_index: Number(form.column_index), col_span: Number(form.col_span),
            display_order: (section.items || []).length,
          } });
          setForm({ definition_id: "", column_index: "0", col_span: "1" }); await onDone();
        });
      }}>
      <Select id={`udf-add-${section.id}`} label="Field" value={form.definition_id} placeholder="Choose a field" error={error}
        onChange={(v) => setForm((f) => ({ ...f, definition_id: v }))}
        options={choices.map((d) => ({ value: d.id, label: `${d.label} (${label(envelope, "data_type", d.data_type)})` }))} />
      <Select id={`udf-add-col-${section.id}`} label="Column" value={form.column_index}
        onChange={(v) => setForm((f) => ({ ...f, column_index: v }))}
        options={[{ value: "0", label: "Left" }, ...(section.column_count === 2 ? [{ value: "1", label: "Right" }] : [])]} />
      <Select id={`udf-add-span-${section.id}`} label="Width" value={form.col_span}
        onChange={(v) => setForm((f) => ({ ...f, col_span: v }))}
        options={[{ value: "1", label: "One column" }, { value: "2", label: "Full width (long text only)" }]} />
      <div className="mb-3"><BusyButton type="submit" busy={busy.busy} className={primaryCls} data-action="addItem">Add field</BusyButton>
        <BusyError error={busy.error} /></div>
    </form>
  );
}

function SectionBlock({ tab, section, index, count, layout, definitions, envelope, write, onMoveSection, reload, handle }) {
  const busy = useBusy();
  const items = section.items || [];
  const sensors = useSensors(useSensor(PointerSensor), useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }));
  const applyOrder = (ids) => busy.run(async () => {
    for (const p of itemOrderPatches(items, ids)) await udfCall("moveItem", { tab: tab.id, item: p.item }, { body: p.body });
    await reload();
  });
  return (
    <div className="mb-3 rounded-md border border-[#ece8dd] bg-white p-3" data-section-id={section.id}>
      <div className="flex flex-wrap items-center gap-2">
        {write ? <span {...handle} className="cursor-grab text-text-muted" aria-label={`Drag section ${section.title || index + 1}`}>⠿</span> : null}
        <h3 className="font-semibold text-navy">{section.title || "Untitled section"}</h3>
        <span className="text-xs text-text-muted">{section.column_count} column(s)</span>
        {write ? (
          <span className="ml-auto flex gap-1">
            <UpDown name={`section ${section.title || index + 1}`} index={index} count={count} onMove={onMoveSection} />
            <BusyButton className={btnCls} busy={busy.busy} data-action="removeSection" aria-label={`Remove section ${section.title || index + 1}`}
              onClick={() => busy.run(async () => { await udfCall("removeSection", { tab: tab.id, section: section.id }); await reload(); })}>
              Remove section
            </BusyButton>
          </span>
        ) : null}
      </div>
      <BusyError error={busy.error} />
      {items.length === 0 ? <Notice>No fields in this section.</Notice> : null}
      <DndContext sensors={sensors} collisionDetection={closestCenter}
        onDragEnd={({ active, over }) => { if (write && over) applyOrder(moveById(items.map((i) => i.id), active.id, over.id)); }}>
        <SortableContext items={items.map((i) => i.id)} strategy={verticalListSortingStrategy}>
          <ul aria-label={`Fields in ${section.title || "section"}`} className="mt-2">
            {items.map((it, i) => (
              <Sortable key={it.id} id={it.id} disabled={!write}>
                {({ handle: h }) => (
                  <div className="flex flex-wrap items-center gap-2 border-b border-border py-1" data-item-id={it.id}>
                    {write ? <span {...h} className="cursor-grab text-text-muted" aria-label={`Drag ${it.label || "item"}`}>⠿</span> : null}
                    <span className="text-sm">{it.label || "Spacer"}</span>
                    <span className="text-xs text-text-muted">
                      {it.column_index === 1 ? "Right" : "Left"}{it.col_span === 2 ? " · full width" : ""}{it.is_read_only ? " · read-only" : ""}
                    </span>
                    {write ? (
                      <span className="ml-auto flex gap-1">
                        <UpDown name={it.label || "item"} index={i} count={items.length}
                          onMove={(idx, d) => applyOrder(moveInList(items.map((x) => x.id), idx, d))} />
                        <BusyButton className={btnCls} busy={busy.busy} data-action="removeItem" aria-label={`Remove ${it.label || "item"}`}
                          onClick={() => busy.run(async () => { await udfCall("removeItem", { tab: tab.id, item: it.id }); await reload(); })}>
                          Remove
                        </BusyButton>
                      </span>
                    ) : null}
                  </div>
                )}
              </Sortable>
            ))}
          </ul>
        </SortableContext>
      </DndContext>
      {write ? <AddItem tab={tab} section={section} layout={layout} definitions={definitions} envelope={envelope} onDone={reload} /> : null}
    </div>
  );
}

export default function LayoutEditor({ tab, envelope, definitions, initialLayout, onChanged }) {
  const write = canWrite(envelope);
  const [layout, setLayout] = useState(initialLayout || null);
  const [loadError, setLoadError] = useState(null);
  const [newSection, setNewSection] = useState({ title: "", column_count: "2" });
  const busy = useBusy();
  const sensors = useSensors(useSensor(PointerSensor), useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }));

  async function reload() {
    try {
      setLayout(await udfCall("layout", { tab: tab.id }));
      setLoadError(null);
    } catch (e) {
      setLoadError(e.message);
    }
    await onChanged?.();
  }
  useEffect(() => { if (!initialLayout) reload(); }, [tab.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const sections = layout?.sections || [];
  const orderSections = (ids) => busy.run(async () => {
    await udfCall("orderSections", { tab: tab.id }, { body: { section_ids: ids } });
    await reload();
  });

  return (
    <div className="mt-4 rounded-md border border-[#ece8dd] bg-[#FAF9F6] p-4" aria-label={`Layout of ${tab.label}`} role="region">
      <h3 className="mb-2 text-base font-semibold text-navy">Layout — {tab.label}</h3>
      {loadError ? <Notice tone="error">{loadError}</Notice> : null}
      {!layout && !loadError ? <Notice>Loading layout…</Notice> : null}
      {layout && sections.length === 0 ? <Notice>This tab has no sections yet.</Notice> : null}
      <BusyError error={busy.error} />
      <DndContext sensors={sensors} collisionDetection={closestCenter}
        onDragEnd={({ active, over }) => { if (write && over) orderSections(moveById(sections.map((s) => s.id), active.id, over.id)); }}>
        <SortableContext items={sections.map((s) => s.id)} strategy={verticalListSortingStrategy}>
          <ul aria-label="Sections" className="p-0">
            {sections.map((s, i) => (
              <Sortable key={s.id} id={s.id} disabled={!write}>
                {({ handle }) => (
                  <SectionBlock tab={tab} section={s} index={i} count={sections.length} layout={layout} definitions={definitions}
                    envelope={envelope} write={write} reload={reload} handle={handle}
                    onMoveSection={(idx, d) => orderSections(moveInList(sections.map((x) => x.id), idx, d))} />
                )}
              </Sortable>
            ))}
          </ul>
        </SortableContext>
      </DndContext>
      {write ? (
        <form className="mt-2 grid grid-cols-1 items-end gap-x-3 md:grid-cols-3" aria-label="Add a section"
          onSubmit={(e) => {
            e.preventDefault();
            busy.run(async () => {
              await udfCall("addSection", { tab: tab.id }, { body: {
                title: newSection.title.trim() || null, column_count: Number(newSection.column_count), display_order: sections.length,
              } });
              setNewSection({ title: "", column_count: "2" }); await reload();
            });
          }}>
          <Text id={`udf-sec-title-${tab.id}`} label="Section title" value={newSection.title}
            onChange={(v) => setNewSection((f) => ({ ...f, title: v }))} />
          <Select id={`udf-sec-cols-${tab.id}`} label="Columns" value={newSection.column_count}
            onChange={(v) => setNewSection((f) => ({ ...f, column_count: v }))}
            options={[{ value: "1", label: "One column" }, { value: "2", label: "Two columns" }]} />
          <div className="mb-3"><BusyButton type="submit" busy={busy.busy} className={primaryCls} data-action="addSection">Add section</BusyButton></div>
        </form>
      ) : null}
    </div>
  );
}
