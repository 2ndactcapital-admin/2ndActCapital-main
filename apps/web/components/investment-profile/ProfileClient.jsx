"use client";

import { useMemo, useState, useTransition } from "react";
import { IconCheck } from "@tabler/icons-react";
import { typeLabel } from "@/lib/entityTypes";
import {
  saveAnswerAction,
  loadAnswersAction,
  loadProfileExtrasAction,
  setProfileModeAction,
} from "@/lib/profileActions";
import FoundationConversation from "@/components/investment-profile/FoundationConversation";
import ExtractionsTab from "@/components/investment-profile/ExtractionsTab";
import BriefTab from "@/components/investment-profile/BriefTab";

const CATEGORIES = [
  { value: "compliance", label: "Compliance" },
  { value: "financial", label: "Financial" },
  { value: "personal", label: "Personal" },
];

const REQUIRED_BORDER = "#9B2335";
const INPUT_BASE =
  "w-full max-w-md rounded-md border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";

function answersToMap(list) {
  const map = {};
  for (const a of list || []) {
    map[a.question_id] = {
      answer_value: a.answer_value ?? null,
      answer_json: a.answer_json ?? null,
    };
  }
  return map;
}

function isAnswered(entry) {
  if (!entry) return false;
  const { answer_value, answer_json } = entry;
  if (answer_value !== null && answer_value !== undefined && answer_value !== "") {
    return true;
  }
  return answer_json !== null && answer_json !== undefined;
}

export default function ProfileClient({
  entities,
  questions,
  foundationQuestions = [],
  defaultEntityId,
  initialAnswers,
  initialMode = "foundation",
  initialConversation = null,
  initialExtractions = [],
  initialBrief = null,
  isStaff = true,
  initialTab = "profile",
}) {
  const [entityId, setEntityId] = useState(defaultEntityId || "");
  const [mode, setMode] = useState(initialMode || "foundation");
  const [tab, setTab] = useState(initialTab);
  const [answers, setAnswers] = useState(() => answersToMap(initialAnswers));
  const [conversation, setConversation] = useState(initialConversation);
  const [extractions, setExtractions] = useState(initialExtractions);
  const [brief, setBrief] = useState(initialBrief);
  const [activeCategory, setActiveCategory] = useState("compliance");
  const [saving, setSaving] = useState({});
  const [loadingEntity, startLoading] = useTransition();

  const standardQuestions = useMemo(
    () => questions.filter((q) => q.category !== "foundation"),
    [questions],
  );
  const answeredCount = useMemo(
    () => standardQuestions.filter((q) => isAnswered(answers[q.id])).length,
    [standardQuestions, answers],
  );
  const total = standardQuestions.length;
  const pct = total === 0 ? 0 : Math.round((answeredCount / total) * 100);

  const entity = entities.find((e) => e.id === entityId);
  const entityName = entity?.display_name || "Client";

  function handleEntityChange(nextId) {
    setEntityId(nextId);
    setAnswers({});
    const next = entities.find((e) => e.id === nextId);
    if (next?.profile_mode) setMode(next.profile_mode);
    startLoading(async () => {
      const [ans, extras] = await Promise.all([
        loadAnswersAction(nextId),
        loadProfileExtrasAction(nextId),
      ]);
      setAnswers(answersToMap(ans.answers));
      setConversation(extras.conversation);
      setExtractions(extras.extractions || []);
      setBrief(extras.brief);
    });
  }

  function switchMode(next) {
    if (next === mode) return;
    if (next === "standard") {
      const ok = window.confirm(
        "Switch to standard questionnaire? Your conversation progress is saved.",
      );
      if (!ok) return;
    }
    setMode(next);
    setProfileModeAction(entityId, next);
  }

  async function save(question, answer_value, answer_json = null) {
    setAnswers((prev) => ({
      ...prev,
      [question.id]: { answer_value, answer_json },
    }));
    setSaving((s) => ({ ...s, [question.id]: "saving" }));
    const res = await saveAnswerAction(entityId, {
      question_id: question.id,
      answer_value,
      answer_json,
    });
    setSaving((s) => ({ ...s, [question.id]: res.ok ? "saved" : "error" }));
  }

  if (!entities || entities.length === 0) {
    return (
      <p className="text-sm text-text-muted">
        No entities found. Create an entity in the CRM first.
      </p>
    );
  }

  const TABS = [
    { key: "profile", label: mode === "foundation" ? "Conversation" : "Standard" },
    { key: "extractions", label: "Extractions" },
    { key: "brief", label: "Brief" },
  ];

  const visible = standardQuestions.filter((q) => q.category === activeCategory);

  return (
    <div>
      {/* Entity selector */}
      <div className="flex flex-wrap items-center gap-3">
        <label className="text-sm font-medium text-text-secondary">Entity</label>
        <select
          value={entityId}
          onChange={(e) => handleEntityChange(e.target.value)}
          className="rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
        >
          {entities.map((e) => (
            <option key={e.id} value={e.id}>
              {e.display_name} ({typeLabel(e.entity_type)})
            </option>
          ))}
        </select>
        {loadingEntity && (
          <span className="text-xs text-text-muted">Loading…</span>
        )}
      </div>

      {/* Mode toggle */}
      <div className="mt-5 flex items-center gap-2">
        {[
          { value: "foundation", label: "Foundation" },
          { value: "standard", label: "Standard" },
        ].map((m) => {
          const active = m.value === mode;
          return (
            <button
              key={m.value}
              type="button"
              onClick={() => switchMode(m.value)}
              className={`rounded-full px-4 py-1.5 text-sm font-medium transition-colors ${
                active
                  ? "bg-navy text-bg-app"
                  : "border border-border bg-bg-card text-text-secondary hover:bg-border"
              }`}
            >
              {m.label}
            </button>
          );
        })}
      </div>

      {/* Tabs */}
      <div className="mt-6 flex flex-wrap gap-1 border-b border-border">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setTab(t.key)}
            className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
              tab === t.key
                ? "border-navy text-navy"
                : "border-transparent text-text-muted hover:text-text-secondary"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Profile tab */}
      {tab === "profile" && (
        <div className="mt-6">
          {mode === "foundation" ? (
            <FoundationConversation
              key={entityId}
              entityId={entityId}
              questions={foundationQuestions}
              initialConversation={conversation}
            />
          ) : (
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-text-muted">
                Standard Questionnaire
              </p>

              {/* Progress */}
              <div className="mt-4">
                <div className="mb-1 flex justify-between text-sm">
                  <span className="text-text-secondary">Profile completion</span>
                  <span className="font-medium text-navy">
                    {answeredCount} of {total} answered
                  </span>
                </div>
                <div className="h-2 w-full overflow-hidden rounded-full bg-border">
                  <div
                    className="h-2 rounded-full bg-gold transition-all"
                    style={{ width: `${pct}%` }}
                  />
                </div>
              </div>

              {/* Category tabs */}
              <div className="mt-6 flex flex-wrap gap-2">
                {CATEGORIES.map((cat) => {
                  const active = cat.value === activeCategory;
                  const count = standardQuestions.filter(
                    (q) => q.category === cat.value,
                  ).length;
                  return (
                    <button
                      key={cat.value}
                      type="button"
                      onClick={() => setActiveCategory(cat.value)}
                      className={`rounded-full px-3 py-1 text-sm font-medium transition-colors ${
                        active
                          ? "bg-navy text-bg-app"
                          : "border border-border bg-bg-card text-text-secondary hover:bg-border"
                      }`}
                    >
                      {cat.label} ({count})
                    </button>
                  );
                })}
              </div>

              {/* Questions */}
              <div className="mt-6 space-y-5">
                {visible.length === 0 && (
                  <p className="text-sm text-text-muted">
                    No questions in this category.
                  </p>
                )}
                {visible.map((q) => {
                  const entry = answers[q.id];
                  const answered = isAnswered(entry);
                  const showRequired = q.is_required && !answered;
                  return (
                    <div
                      key={`${entityId}-${q.id}`}
                      className="rounded-lg border border-border bg-bg-card p-4"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <label className="text-sm font-medium text-text-primary">
                          {q.question_text}
                          {q.is_required && (
                            <span className="ml-1 text-text-muted">*</span>
                          )}
                        </label>
                        <div className="flex shrink-0 items-center gap-2">
                          {saving[q.id] === "saving" && (
                            <span className="text-xs text-text-muted">Saving…</span>
                          )}
                          {saving[q.id] === "error" && (
                            <span className="text-xs text-[#9B2335]">Save failed</span>
                          )}
                          {answered && <IconCheck size={18} className="text-gold" />}
                        </div>
                      </div>
                      <div className="mt-3">
                        <QuestionField
                          question={q}
                          entry={entry}
                          showRequired={showRequired}
                          onSave={save}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Extractions tab */}
      {tab === "extractions" && (
        <div className="mt-6">
          <ExtractionsTab
            key={entityId}
            entityId={entityId}
            initialExtractions={extractions}
          />
        </div>
      )}

      {/* Brief tab */}
      {tab === "brief" && (
        <div className="mt-6">
          <BriefTab
            key={entityId}
            entityId={entityId}
            entityName={entityName}
            initialBrief={brief}
            canGenerate={isStaff}
          />
        </div>
      )}
    </div>
  );
}

function QuestionField({ question, entry, showRequired, onSave }) {
  const value = entry?.answer_value ?? "";
  const borderStyle = showRequired ? { borderColor: REQUIRED_BORDER } : undefined;

  if (question.question_type === "boolean") {
    return (
      <div className="flex gap-2">
        {[
          ["true", "Yes"],
          ["false", "No"],
        ].map(([val, label]) => {
          const active = value === val;
          return (
            <button
              key={val}
              type="button"
              onClick={() => onSave(question, val)}
              style={!active && showRequired ? borderStyle : undefined}
              className={`rounded-md border px-4 py-1.5 text-sm font-medium transition-colors ${
                active
                  ? "border-navy bg-navy text-bg-app"
                  : "border-border bg-bg-card text-text-secondary hover:bg-border"
              }`}
            >
              {label}
            </button>
          );
        })}
      </div>
    );
  }

  if (question.question_type === "select") {
    const options = question.options?.options || [];
    return (
      <select
        defaultValue={value}
        style={borderStyle}
        onChange={(e) => onSave(question, e.target.value)}
        className={`${INPUT_BASE} ${showRequired ? "" : "border-border"}`}
      >
        <option value="">Select…</option>
        {options.map((opt) => (
          <option key={opt} value={opt}>
            {opt}
          </option>
        ))}
      </select>
    );
  }

  const inputType = question.question_type === "number" ? "number" : "text";
  return (
    <input
      type={inputType}
      defaultValue={value}
      style={borderStyle}
      onBlur={(e) => {
        if (e.target.value !== value) onSave(question, e.target.value);
      }}
      className={`${INPUT_BASE} ${showRequired ? "" : "border-border"}`}
    />
  );
}
