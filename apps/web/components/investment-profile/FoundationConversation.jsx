"use client";

import { useEffect, useRef, useState } from "react";
import {
  startConversationAction,
  sendMessageAction,
} from "@/lib/profileActions";

export default function FoundationConversation({
  entityId,
  questions = [],
  initialConversation = null,
}) {
  const total = questions.length || 10;
  const [messages, setMessages] = useState(
    initialConversation?.messages || [],
  );
  const [questionIndex, setQuestionIndex] = useState(
    initialConversation?.current_question_index ?? 0,
  );
  const [isComplete, setIsComplete] = useState(
    initialConversation?.status === "completed",
  );
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [starting, setStarting] = useState(!initialConversation);
  const [error, setError] = useState(null);
  const scrollRef = useRef(null);

  // Start a conversation on mount if none exists yet.
  useEffect(() => {
    let active = true;
    if (!initialConversation) {
      startConversationAction(entityId).then((res) => {
        if (!active) return;
        if (res.ok) {
          setMessages(res.conversation.messages || []);
          setQuestionIndex(res.conversation.current_question_index ?? 0);
          setIsComplete(res.conversation.status === "completed");
        } else {
          setError(res.error);
        }
        setStarting(false);
      });
    }
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entityId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, starting]);

  async function send() {
    const text = input.trim();
    if (!text || sending || isComplete) return;
    setInput("");
    setError(null);
    // Optimistically show the user message.
    setMessages((prev) => [
      ...prev,
      { role: "user", content: text, question_index: questionIndex },
    ]);
    setSending(true);
    const res = await sendMessageAction(entityId, text);
    setSending(false);
    if (!res.ok) {
      setError(res.error);
      return;
    }
    const r = res.result;
    setMessages((prev) => [
      ...prev,
      { role: "assistant", content: r.message, question_index: r.question_index },
    ]);
    setQuestionIndex(r.question_index);
    setIsComplete(r.is_complete);
  }

  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  const shownIndex = Math.min(questionIndex + (isComplete ? 0 : 1), total);
  const pct = Math.round((Math.min(questionIndex, total) / total) * 100);
  const currentQuestion = questions[Math.min(questionIndex, total - 1)];

  return (
    <div>
      {/* Progress */}
      <div className="mb-4">
        <div className="mb-1 flex items-center justify-between">
          <span className="text-sm font-medium text-gold">
            {isComplete ? `All ${total} questions` : `Question ${shownIndex} of ${total}`}
          </span>
          {currentQuestion && !isComplete && (
            <span className="truncate text-xs text-text-muted">
              {currentQuestion.question_text}
            </span>
          )}
        </div>
        <div className="h-2 w-full overflow-hidden rounded-full bg-border">
          <div
            className="h-2 rounded-full transition-all"
            style={{ width: `${pct}%`, backgroundColor: "var(--2a-gold)" }}
          />
        </div>
      </div>

      {/* Messages */}
      <div
        ref={scrollRef}
        className="max-h-[460px] space-y-4 overflow-y-auto rounded-lg border border-border bg-bg-card p-5"
      >
        {starting ? (
          <p className="text-center text-sm text-text-muted">
            Starting your conversation…
          </p>
        ) : (
          messages.map((m, i) => (
            <div
              key={i}
              className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className="max-w-[80%] whitespace-pre-line text-sm"
                style={{
                  borderRadius: 6,
                  padding: "12px 16px",
                  backgroundColor: m.role === "user" ? "var(--2a-navy)" : "var(--2a-bg)",
                  color: m.role === "user" ? "var(--2a-bg)" : "var(--2a-text)",
                }}
              >
                {m.content}
              </div>
            </div>
          ))
        )}
        {sending && (
          <div className="flex justify-start">
            <div
              className="text-sm text-text-muted"
              style={{ borderRadius: 6, padding: "12px 16px", backgroundColor: "var(--2a-bg)" }}
            >
              …
            </div>
          </div>
        )}
      </div>

      {error && <p className="mt-2 text-sm text-[#9B2335]">{error}</p>}

      {/* Complete banner or input */}
      {isComplete ? (
        <div
          className="mt-4 flex items-center justify-between rounded-lg px-4 py-3"
          style={{ backgroundColor: "var(--2a-gold-light)", color: "var(--2a-navy)" }}
        >
          <span className="text-sm font-medium">Conversation complete ✓</span>
          <a
            href="/investment-profile?tab=brief"
            className="text-sm font-semibold underline"
          >
            View your profile summary →
          </a>
        </div>
      ) : (
        <div className="mt-4">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            rows={3}
            placeholder="Share your thoughts..."
            disabled={sending || starting}
            className="w-full resize-y rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
            style={{ minHeight: 76, maxHeight: 160 }}
          />
          <div className="mt-2 flex items-center justify-between">
            <span className="text-xs text-text-muted">
              Enter to send · Shift+Enter for a new line
            </span>
            <button
              type="button"
              onClick={send}
              disabled={sending || starting || !input.trim()}
              className="rounded-md bg-navy px-5 py-2 text-sm font-medium text-bg-app transition-opacity hover:opacity-90 disabled:opacity-60"
            >
              {sending ? "Sending…" : "Send"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
