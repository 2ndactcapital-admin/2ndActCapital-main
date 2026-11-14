"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import ActivityView from "./ActivityView";
import BoundedChoice from "./BoundedChoice";
import RenderDirective from "./RenderDirective";
import { usePermissions } from "@/lib/usePermissions";
import { useAssistantPanel, ASK_EVENT } from "@/lib/useAssistantPanel";
import { useEntityContext } from "@/lib/useEntityContext";
import { assistantContextFor } from "@/lib/commandPalette.mjs";
import { typeLabel } from "@/lib/entityContext.mjs";

// permission: null means available to all authenticated users.
const VIEW_CONFIG = [
  { key: "Chat", label: "AI Assistant", permission: null },
  { key: "Todos", label: "To-Do", permission: null },
  { key: "Activity", label: "Activity", permission: null },
  { key: "Interesting Deals", label: "Interesting Deals", permission: null },
  { key: "Messages", label: "Messages", permission: null },
];

// NAV1 — the role → posture table and usePosture() are gone: the panel is CLOSED by default for everyone, the ONE
// top-bar button (components/assistant/AssistantToggle.jsx) opens / closes it, and the choice is remembered in
// users.assistant_panel_posture (lib/assistantPanelPref.mjs, lib/useAssistantPanel.js). The old code read the
// posture and the role off the Auth0 session user, which carries neither — every user fell to "member" → open.

function ChatView({ contextRef }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [convId, setConvId] = useState(null);
  const [proposedAction, setProposedAction] = useState(null);
  const bottomRef = useRef(null);
  const router = useRouter();

  useEffect(() => {
    async function loadConversation() {
      try {
        const params = new URLSearchParams();
        if (contextRef?.type) params.set("context_type", contextRef.type);
        if (contextRef?.id) params.set("context_id", contextRef.id);
        const res = await fetch(`/api/assistant/conversation?${params}`);
        if (res.ok) {
          const data = await res.json();
          setConvId(data.id);
          setMessages(data.messages || []);
        }
      } catch (e) {
        console.error("AssistantPanel: load conversation", e);
      }
    }
    loadConversation();
  }, [contextRef?.type, contextRef?.id]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // NAV2 — "Ask about <entity>…" from Ctrl K hands the chat a draft to finish.
  const inputRef = useRef(null);
  useEffect(() => {
    function onDraft(e) {
      setInput(e.detail?.draft || "");
      setTimeout(() => inputRef.current?.focus(), 0);
    }
    window.addEventListener(ASK_EVENT, onDraft);
    return () => window.removeEventListener(ASK_EVENT, onDraft);
  }, []);

  async function send() {
    const text = input.trim();
    if (!text || loading) return;
    setInput("");
    setProposedAction(null);
    setLoading(true);

    const userMsg = { role: "user", text };
    setMessages((prev) => [...prev, userMsg]);

    try {
      const res = await fetch("/api/assistant/message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          context_ref: contextRef || null,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Request failed");

      const assistantMsg = {
        role: "assistant",
        text: data.message,
        disclosures: data.disclosures || [],
        render: data.render || null,
      };
      setMessages((prev) => [...prev, assistantMsg]);
      if (data.proposed_action) setProposedAction(data.proposed_action);
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", text: "Something went wrong. Please try again.", disclosures: [], render: null },
      ]);
    } finally {
      setLoading(false);
    }
  }

  function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  function handleNavigate(route) {
    router.push(route);
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {/* Message list */}
      <div className="flex-1 overflow-y-auto px-3 py-3 space-y-3">
        {messages.length === 0 && (
          <p className="text-center text-xs text-[var(--2a-text-muted)] mt-8">
            Ask anything about your portfolio, the marketplace, or your network.
          </p>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            className={m.role === "user" ? "flex justify-end" : "flex justify-start"}
          >
            <div
              className={[
                "max-w-[85%] rounded-lg px-3 py-2 text-sm",
                m.role === "user"
                  ? "bg-[var(--2a-navy)] text-white"
                  : "bg-white border border-[#ece8dd] text-[var(--2a-text)]",
              ].join(" ")}
            >
              <p className="whitespace-pre-wrap">{m.text}</p>
              {m.disclosures?.length > 0 && (
                <ul className="mt-1 space-y-0.5">
                  {m.disclosures.map((d, j) => (
                    <li key={j} className="text-xs text-[var(--2a-text-muted)] italic">
                      {d}
                    </li>
                  ))}
                </ul>
              )}
              {m.render && (
                <RenderDirective render={m.render} onNavigate={handleNavigate} />
              )}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="rounded-lg border border-[#ece8dd] bg-white px-3 py-2 text-sm text-[var(--2a-text-muted)]">
              …
            </div>
          </div>
        )}
        {proposedAction && (
          <div className="flex justify-start w-full">
            <div className="w-full max-w-[92%] rounded-lg border border-[#ece8dd] bg-white px-3 py-2">
              <BoundedChoice
                proposedAction={proposedAction}
                onResolved={() => setProposedAction(null)}
                onNavigate={handleNavigate}
              />
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Composer */}
      <div className="border-t border-[var(--2a-border)] px-3 py-2">
        <textarea
          ref={inputRef}
          aria-label="Message the assistant"
          className="w-full resize-none rounded border border-[var(--2a-border)] bg-[var(--2a-bg)] px-2 py-1.5 text-sm text-[var(--2a-text)] placeholder-[var(--2a-text-muted)] focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)]"
          rows={2}
          placeholder="Message…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={loading}
        />
      </div>
    </div>
  );
}

function TodosView() {
  const [data, setData] = useState({ actual: [], anticipated: [] });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    fetch("/api/dashboard/todos")
      .then((r) => r.ok ? r.json() : { actual: [], anticipated: [] })
      .then((d) => { if (active) { setData(d); setLoading(false); } })
      .catch(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  async function dismiss(id) {
    setData((prev) => ({
      actual: prev.actual.filter((t) => t.id !== id),
      anticipated: prev.anticipated.filter((t) => t.id !== id),
    }));
    await fetch(`/api/dashboard/todos/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dismissed: true }),
    }).catch(() => {});
  }

  if (loading) {
    return <p className="px-3 py-6 text-center text-xs text-[var(--2a-text-muted)]">Loading…</p>;
  }

  const noItems = data.actual.length === 0 && data.anticipated.length === 0;
  if (noItems) {
    return <p className="px-3 py-6 text-center text-xs text-[var(--2a-text-muted)]">Nothing pending.</p>;
  }

  function Section({ label, items }) {
    if (!items.length) return null;
    return (
      <div className="mb-3">
        <p className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-[var(--2a-gold)]">
          {label}
        </p>
        {items.map((t) => (
          <div key={t.id} className="flex items-start gap-2 border-b border-[var(--2a-bg-sidebar)] px-3 py-2 last:border-0">
            <div className="flex-1 min-w-0">
              <p className="text-xs font-medium text-[var(--2a-text)] truncate">{t.title}</p>
              {t.detail && <p className="text-[10px] text-[var(--2a-text-muted)] mt-0.5">{t.detail}</p>}
            </div>
            <button
              type="button"
              onClick={() => dismiss(t.id)}
              className="text-[10px] text-[var(--2a-text-muted)] hover:text-[var(--2a-text)] shrink-0 mt-0.5"
            >
              ✕
            </button>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto py-2">
      <Section label="Needs your attention" items={data.actual} />
      <Section label="On the horizon" items={data.anticipated} />
    </div>
  );
}

/**
 * The panel itself — rendered only while open (closed = nothing at all; the top-bar button is the one control).
 * Exported so node tests render it from a fed `open` without a browser.
 */
export function AssistantPanelView({ open, contextRef, contextLabel = null }) {
  const [activeView, setActiveView] = useState("Chat");
  const { can } = usePermissions();

  // Filter views by the current user's permissions.
  const availableViews = VIEW_CONFIG.filter(
    (v) => !v.permission || can(v.permission)
  );

  // Derive context from current route if no explicit contextRef
  const effectiveContext = contextRef || null;

  if (!open) return null;

  return (
    <aside
      id="assistant-panel"
      data-testid="assistant-panel"
      aria-label="Assistant"
      className="flex w-72 flex-col overflow-hidden border-l border-[var(--2a-border)] bg-white"
    >
      <div className="flex items-center border-b border-[var(--2a-border)] px-3 py-2">
        <span className="text-xs font-semibold uppercase tracking-widest text-[var(--2a-gold)]">
          Assistant
        </span>
      </div>

      {/* NAV2 — the selected entity is the assistant's context (shown so the person knows what "this" means) */}
      {contextLabel && (
        <div className="border-b border-[var(--2a-border)] px-3 py-1.5 text-xs text-[var(--2a-text-muted)]" data-testid="assistant-context">
          About: <span className="text-[var(--2a-navy)]">{contextLabel}</span>
        </div>
      )}

      {/* View selector — single dropdown, role-gated */}
      <div className="border-b border-[var(--2a-border)] px-2 py-1.5">
        <select
          value={activeView}
          onChange={(e) => setActiveView(e.target.value)}
          className="w-full rounded border border-[var(--2a-border)] bg-[var(--2a-bg)] px-2 py-1 text-xs font-medium text-[var(--2a-navy)] focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)]"
        >
          {availableViews.map((v) => (
            <option key={v.key} value={v.key}>
              {v.label}
            </option>
          ))}
        </select>
      </div>

      {/* View body */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {activeView === "Chat" && (
          <ChatView contextRef={effectiveContext} />
        )}
        {activeView === "Todos" && <TodosView />}
        {activeView === "Activity" && <ActivityView />}
        {(activeView === "Interesting Deals" || activeView === "Messages") && (
          <p className="px-4 py-6 text-center text-sm text-[var(--2a-text-muted)]">Coming soon.</p>
        )}
      </div>
    </aside>
  );
}

/**
 * NAV2 — with no explicit contextRef the panel takes the ONE selected entity (only a VISIBLE one) as its context, in
 * the EXISTING request shape: context_ref { type: "entity", id } (+ entity_type). The server re-checks visibility.
 */
export function entityContextRef(explicit, state) {
  if (explicit) return { ref: explicit, label: null };
  const ref = assistantContextFor(state);
  return { ref, label: ref ? `${typeLabel(state.type) ? `${typeLabel(state.type)} · ` : ""}${state.name}` : null };
}

function PanelWithEntity({ open, contextRef }) {
  const { state } = useEntityContext();
  const { ref, label } = entityContextRef(contextRef, state);
  return <AssistantPanelView open={open} contextRef={ref} contextLabel={label} />;
}

export default function AssistantPanel({ contextRef }) {
  const { open } = useAssistantPanel();
  return (
    <Suspense fallback={<AssistantPanelView open={open} contextRef={contextRef} />}>
      <PanelWithEntity open={open} contextRef={contextRef} />
    </Suspense>
  );
}
