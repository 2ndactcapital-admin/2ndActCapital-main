import { BUTTON, CARD, CARD_STYLE, CONTROL, ERROR_STYLE, QUIET } from "@/components/market/marketStyles.mjs";
import { canSaveView, canWriteViews, deleteViewConfirmText, viewEntries, viewNameMaxLength } from "@/lib/market/viewsModel.mjs";

/**
 * The "Saved views" card (mkt04c Task 4), drawn from state it is given. No
 * hooks: tests call it directly and fire its handlers.
 *
 * Presets, then the caller's own views, each a button that loads it (the
 * active one is marked). WRITE CONTROLS — Save (name field and button),
 * Update, Delete and the confirm row — render only when the views envelope
 * says can_write === true (canWriteViews), and Update / Delete never for a
 * preset. A failure to load shows its message here and nowhere else.
 *
 * Props: state (interpretViews state | {kind: "loading"}), activeId, notices
 * ([text]), form ({name, saving, error}), pendingDelete ({id, busy, error} |
 * null), updateError, updating, and the handlers onLoad(view), onUpdate(id),
 * onDeleteRequest(id), onDeleteConfirm, onDeleteCancel, onNameChange(text),
 * onSave.
 */
export default function SavedViewsView({
  state,
  activeId,
  notices,
  form,
  pendingDelete,
  updateError,
  updating,
  onLoad,
  onUpdate,
  onDeleteRequest,
  onDeleteConfirm,
  onDeleteCancel,
  onNameChange,
  onSave,
}) {
  const writable = canWriteViews(state);
  const entries = viewEntries(state);
  const pendingName = pendingDelete ? entries.find((e) => e.id === pendingDelete.id)?.name ?? "" : "";

  let body;
  if (state?.kind === "ready") {
    body = entries.length ? (
      <ul className="space-y-1" data-views="list">
        {entries.map((e) => {
          const active = e.id === activeId;
          return (
            <li key={e.id} className="flex items-center gap-1.5" data-view-id={e.id} data-view-kind={e.preset ? "preset" : "user"}>
              <button
                type="button"
                aria-pressed={active}
                onClick={() => onLoad(e.view)}
                className={`min-w-0 flex-1 truncate rounded border px-2 py-1 text-left text-sm ${
                  active
                    ? "border-[var(--2a-gold)] font-semibold text-[var(--2a-navy)]"
                    : "border-transparent text-[var(--2a-text)] hover:border-[var(--2a-border)]"
                }`}
                data-view-control="load"
              >
                {e.name}
              </button>
              {writable && e.canUpdate && active && (
                <button type="button" className={BUTTON} disabled={updating === true} onClick={() => onUpdate(e.id)} data-view-control="update">
                  Update
                </button>
              )}
              {writable && e.canDelete && (
                <button
                  type="button"
                  className={BUTTON}
                  aria-label={`Delete ${e.name}`}
                  onClick={() => onDeleteRequest(e.id)}
                  data-view-control="delete"
                >
                  Delete
                </button>
              )}
            </li>
          );
        })}
      </ul>
    ) : (
      <p className={QUIET}>No saved views yet.</p>
    );
  } else if (state?.kind === "error") {
    body = (
      <p className="text-xs" style={{ color: ERROR_STYLE.color }} role="alert" data-views="error">
        {state.message}
      </p>
    );
  } else {
    body = <p className={QUIET}>Loading views…</p>;
  }

  return (
    <section className={`${CARD} min-w-0 space-y-3 p-4`} style={CARD_STYLE} data-market="saved-views">
      <h2 className="text-base font-semibold text-[var(--2a-navy)]" style={{ fontFamily: "Spectral, Georgia, serif" }}>
        Saved views
      </h2>
      {body}

      {writable && updateError && (
        <p className="text-xs" style={{ color: ERROR_STYLE.color }} role="alert" data-views="update-error">
          {updateError}
        </p>
      )}

      {writable && pendingDelete && (
        <div className="space-y-1.5" role="alertdialog" aria-label="Confirm delete" data-views="confirm">
          <p className="text-sm text-[var(--2a-text)]">{deleteViewConfirmText(pendingName)}</p>
          <div className="flex gap-1.5">
            <button
              type="button"
              className={BUTTON}
              disabled={pendingDelete.busy === true}
              onClick={onDeleteConfirm}
              data-view-control="confirm-delete"
            >
              Delete
            </button>
            <button type="button" className={BUTTON} onClick={onDeleteCancel} data-view-control="cancel-delete">
              Cancel
            </button>
          </div>
          {pendingDelete.error && (
            <p className="text-xs" style={{ color: ERROR_STYLE.color }} role="alert" data-views="delete-error">
              {pendingDelete.error}
            </p>
          )}
        </div>
      )}

      {notices?.length > 0 && (
        <ul className="space-y-0.5 text-xs text-[var(--2a-text-secondary)]" data-views="notices">
          {notices.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}

      {writable && (
        <form
          className="space-y-1.5 border-t border-[var(--2a-border)] pt-3"
          onSubmit={(e) => {
            e.preventDefault();
            onSave();
          }}
          data-views="save-form"
        >
          <div className="flex gap-1.5">
            <input
              type="text"
              className={`${CONTROL} min-w-0 flex-1`}
              value={form.name}
              maxLength={viewNameMaxLength(state)}
              placeholder="Name this view"
              aria-label="View name"
              onChange={(e) => onNameChange(e.target.value)}
              data-view-control="name"
            />
            <button type="submit" className={BUTTON} disabled={!canSaveView(form)} data-view-control="save">
              Save view
            </button>
          </div>
          {form.error && (
            <p className="text-xs" style={{ color: ERROR_STYLE.color }} role="alert" data-views="save-error">
              {form.error}
            </p>
          )}
        </form>
      )}
    </section>
  );
}
