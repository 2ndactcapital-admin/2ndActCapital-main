"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import ActivityView from "./ActivityView";
import BoundedChoice from "./BoundedChoice";
import RenderDirective from "./RenderDirective";
import { usePermissions } from "@/lib/usePermissions";

// permission: null means available to all authenticated users.
const VIEW_CONFIG = [
  { key: "Chat", label: "AI Assistant", permission: null },
  { key: "Todos", label: "To-Do", permission: null },
  { key: "Activity", label: "Activity", permission: null },
  { key: "Interesting Deals", label: "Interesting Deals", permission: null },
  { key: "Messages", label: "Messages", permission: null },
];

// Map user role → default posture when no user override is set.
const ROLE_POSTURE = {
  member: "expanded",
  next_gen: "expanded",
  admin: "collapsible",
  super_admin: "collapsible",
  advisor: "collapsible",
  investment_staff: "collapsible",
  support_staff: "collapsed",
  compliance_jr: "collapsed",
  compliance_sr: "collapsed",
  fund_finance: "collapsed",
  ir_member_relations: "collapsed",
  investment_committee: "collapsible",
  member_manager: "collapsible",
};

function usePosture(user) {
  const [posture, setPosture] = useState("collapsed");

  useEffect(() => {
    if (!user) return;
    // User override wins
    if (user.assistant_panel_posture) {
      setPosture(user.assistant_panel_posture);
      return;
    }
    // Role-based default
    const role = user.role || "member";
    setPosture(ROLE_POSTURE[role] || "collapsible");
  }, [user]);

  return [posture, setPosture];
}

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
          <p className="text-center text-xs text-[#64748B] mt-8">
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
                  ? "bg-[#1B2B4B] text-white"
                  : "bg-white border border-[#ece8dd] text-[#0F172A]",
              ].join(" ")}
            >
              <p className="whitespace-pre-wrap">{m.text}</p>
              {m.disclosures?.length > 0 && (
                <ul className="mt-1 space-y-0.5">
                  {m.disclosures.map((d, j) => (
                    <li key={j} className="text-xs text-[#64748B] italic">
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
            <div className="rounded-lg border border-[#ece8dd] bg-white px-3 py-2 text-sm text-[#64748B]">
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
      <div className="border-t border-[#E2E8F0] px-3 py-2">
        <textarea
          className="w-full resize-none rounded border border-[#E2E8F0] bg-[#FAF9F6] px-2 py-1.5 text-sm text-[#0F172A] placeholder-[#64748B] focus:outline-none focus:ring-1 focus:ring-[#C5A880]"
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
    return <p className="px-3 py-6 text-center text-xs text-[#64748B]">Loading…</p>;
  }

  const noItems = data.actual.length === 0 && data.anticipated.length === 0;
  if (noItems) {
    return <p className="px-3 py-6 text-center text-xs text-[#64748B]">Nothing pending.</p>;
  }

  function Section({ label, items }) {
    if (!items.length) return null;
    return (
      <div className="mb-3">
        <p className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-[#C5A880]">
          {label}
        </p>
        {items.map((t) => (
          <div key={t.id} className="flex items-start gap-2 border-b border-[#F5F1EB] px-3 py-2 last:border-0">
            <div className="flex-1 min-w-0">
              <p className="text-xs font-medium text-[#0F172A] truncate">{t.title}</p>
              {t.body && <p className="text-[10px] text-[#64748B] mt-0.5">{t.body}</p>}
            </div>
            <button
              type="button"
              onClick={() => dismiss(t.id)}
              className="text-[10px] text-[#64748B] hover:text-[#0F172A] shrink-0 mt-0.5"
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

export default function AssistantPanel({ user, contextRef }) {
  const [posture, setPosture] = usePosture(user);
  const [expanded, setExpanded] = useState(false);
  const [activeView, setActiveView] = useState("Chat");
  const pathname = usePathname();
  const { can, loading: permsLoading } = usePermissions();

  // Filter views by the current user's permissions.
  const availableViews = VIEW_CONFIG.filter(
    (v) => !v.permission || can(v.permission)
  );

  // Derive context from current route if no explicit contextRef
  const effectiveContext = contextRef || null;

  // Resolve initial open state from posture
  useEffect(() => {
    setExpanded(posture === "expanded");
  }, [posture]);

  if (posture === "collapsed") {
    // Thin rail — click to expand
    return (
      <div
        className="flex w-10 cursor-pointer flex-col items-center border-l border-[#E2E8F0] bg-white pt-4"
        title="Open assistant"
        onClick={() => {
          setPosture("expanded");
          setExpanded(true);
        }}
      >
        <span className="text-[#C5A880]" style={{ writingMode: "vertical-rl", transform: "rotate(180deg)", fontSize: 11, letterSpacing: "0.15em" }}>
          ASSISTANT
        </span>
      </div>
    );
  }

  return (
    <div
      className={[
        "flex flex-col border-l border-[#E2E8F0] bg-white transition-all duration-200",
        expanded ? "w-72" : "w-10",
      ].join(" ")}
    >
      {/* Header / toggle */}
      <div
        className="flex cursor-pointer items-center justify-between border-b border-[#E2E8F0] px-3 py-2"
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded && (
          <span className="text-xs font-semibold uppercase tracking-widest text-[#C5A880]">
            Assistant
          </span>
        )}
        <span className="ml-auto text-[#64748B] text-xs">{expanded ? "›" : "‹"}</span>
      </div>

      {expanded && (
        <>
          {/* View selector — single dropdown, role-gated */}
          <div className="border-b border-[#E2E8F0] px-2 py-1.5">
            <select
              value={activeView}
              onChange={(e) => setActiveView(e.target.value)}
              className="w-full rounded border border-[#E2E8F0] bg-[#FAF9F6] px-2 py-1 text-xs font-medium text-[#1B2B4B] focus:outline-none focus:ring-1 focus:ring-[#C5A880]"
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
              <p className="px-4 py-6 text-center text-sm text-[#64748B]">Coming soon.</p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
