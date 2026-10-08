import { BUTTON, CONTROL, ERROR_STYLE, EYEBROW } from "@/components/market/marketStyles.mjs";
import { canSave, deleteConfirmText } from "@/lib/market/customDates.mjs";
import {
  EDGE_END,
  EDGE_START,
  SOURCE_KEY,
  SOURCE_MY,
  customDateOptions,
  keyDateOptions,
} from "@/lib/market/keyDatesModel.mjs";

const EDGES = [
  { key: EDGE_START, label: "Start of period" },
  { key: EDGE_END, label: "End of period" },
];

/**
 * The key-dates bar under the chart (mkt04c Task 3), drawn from state it is
 * given. No hooks: tests call it directly and fire its handlers.
 *
 *   Key dates   the reference list, in the server's order; a range entry adds
 *               Start of period / End of period.
 *   My dates    the caller's own dates (disabled, "None saved yet", when
 *               empty); Add opens the inline form; Delete appears only while a
 *               personal date is picked and asks before anything is sent.
 *
 * WRITE CONTROLS (Add, the form, Delete, the confirm row) render only when
 * canWrite is exactly true — the container passes canWriteDates(keyDates),
 * which needs the key-dates envelope's can_write === true.
 *
 * Props: keyDates (interpretKeyDates state), dateSel, resolved
 * (resolveDateSelection), canWrite, nameMax, form, pendingDelete, and the
 * handlers onPick(source, id), onEdge(edge), onOpenForm, onFormEdit(field,
 * value), onFormSave, onFormCancel, onDeleteRequest(id), onDeleteConfirm,
 * onDeleteCancel.
 */
export default function KeyDatesBar({
  keyDates,
  dateSel,
  resolved,
  canWrite,
  nameMax,
  form,
  pendingDelete,
  onPick,
  onEdge,
  onOpenForm,
  onFormEdit,
  onFormSave,
  onFormCancel,
  onDeleteRequest,
  onDeleteConfirm,
  onDeleteCancel,
}) {
  if (keyDates?.kind !== "ready") return null;
  const writable = canWrite === true;
  const keyOptions = keyDateOptions(keyDates.keyDates);
  const myOptions = customDateOptions(keyDates.customDates);
  const keyValue = dateSel?.source === SOURCE_KEY ? dateSel.id : "";
  const myValue = dateSel?.source === SOURCE_MY ? dateSel.id : "";
  const showEdges = resolved?.source === SOURCE_KEY && resolved.isRange;
  const pendingName = pendingDelete ? keyDates.customDates.find((c) => c?.id === pendingDelete.id)?.name ?? "" : "";

  return (
    <div className="space-y-2" data-market="key-dates-bar">
      <div className="flex flex-wrap items-end gap-4">
        <label className="flex min-w-0 flex-col gap-1">
          <span className={EYEBROW}>Key dates</span>
          <select
            className={`${CONTROL} max-w-[26rem]`}
            value={keyValue}
            onChange={(e) => onPick(SOURCE_KEY, e.target.value)}
            data-key-dates="key"
          >
            <option value="">Choose a key date</option>
            {keyOptions.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>

        {showEdges && (
          <div className="flex" role="group" aria-label="Which end of the period" data-key-dates="edges">
            {EDGES.map((edge, i) => {
              const on = resolved.edge === edge.key;
              return (
                <button
                  key={edge.key}
                  type="button"
                  aria-pressed={on}
                  onClick={() => onEdge(edge.key)}
                  data-key-dates-edge={edge.key}
                  className={`border border-[var(--2a-border)] px-2.5 py-1 text-xs ${i === 0 ? "rounded-l" : "-ml-px rounded-r"} ${
                    on ? "bg-[var(--2a-navy)] text-white" : "bg-white text-[var(--2a-text-secondary)]"
                  }`}
                >
                  {edge.label}
                </button>
              );
            })}
          </div>
        )}

        <label className="flex min-w-0 flex-col gap-1">
          <span className={EYEBROW}>My dates</span>
          <select
            className={`${CONTROL} max-w-[20rem]`}
            value={myValue}
            disabled={myOptions.length === 0}
            onChange={(e) => onPick(SOURCE_MY, e.target.value)}
            data-key-dates="my"
          >
            {myOptions.length === 0 ? (
              <option value="">None saved yet</option>
            ) : (
              <>
                <option value="">Choose one of your dates</option>
                {myOptions.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </>
            )}
          </select>
        </label>

        {writable && (
          <div className="flex items-center gap-1.5">
            <button type="button" className={BUTTON} onClick={onOpenForm} disabled={form?.open === true} data-key-dates="add">
              Add
            </button>
            {myValue !== "" && resolved?.source === SOURCE_MY && !pendingDelete && (
              <button type="button" className={BUTTON} onClick={() => onDeleteRequest(myValue)} data-key-dates="delete">
                Delete
              </button>
            )}
          </div>
        )}
      </div>

      {writable && form?.open === true && (
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            onFormSave();
          }}
          data-key-dates="add-form"
        >
          <label className="flex flex-col gap-1">
            <span className={EYEBROW}>Name</span>
            <input
              type="text"
              className={CONTROL}
              value={form.name}
              maxLength={nameMax}
              onChange={(e) => onFormEdit("name", e.target.value)}
              data-key-dates-field="name"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className={EYEBROW}>Date</span>
            <input
              type="date"
              className={CONTROL}
              value={form.date}
              onChange={(e) => onFormEdit("date", e.target.value)}
              data-key-dates-field="date"
            />
          </label>
          <button type="submit" className={BUTTON} disabled={!canSave(form)} data-key-dates="save">
            Save date
          </button>
          <button type="button" className={BUTTON} onClick={onFormCancel} data-key-dates="cancel">
            Cancel
          </button>
          {form.error && (
            <p className="w-full text-xs" style={{ color: ERROR_STYLE.color }} role="alert" data-key-dates="form-error">
              {form.error}
            </p>
          )}
        </form>
      )}

      {writable && pendingDelete && (
        <div className="flex flex-wrap items-center gap-2" role="alertdialog" aria-label="Confirm delete" data-key-dates="confirm">
          <span className="text-sm text-[var(--2a-text)]">{deleteConfirmText(pendingName)}</span>
          <button
            type="button"
            className={BUTTON}
            disabled={pendingDelete.busy === true}
            onClick={onDeleteConfirm}
            data-key-dates="confirm-delete"
          >
            Delete
          </button>
          <button type="button" className={BUTTON} onClick={onDeleteCancel} data-key-dates="cancel-delete">
            Cancel
          </button>
          {pendingDelete.error && (
            <p className="w-full text-xs" style={{ color: ERROR_STYLE.color }} role="alert" data-key-dates="delete-error">
              {pendingDelete.error}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
