"use client";

import { useMemo, useState, useTransition } from "react";
import { IconCheck } from "@tabler/icons-react";
import { typeLabel } from "@/lib/entityTypes";
import { saveAnswerAction, loadAnswersAction } from "@/lib/profileActions";

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

export default function ProfileClient({ entities, questions, defaultEntityId, initialAnswers }) {
  const [entityId, setEntityId] = useState(defaultEntityId || "");
  const [answers, setAnswers] = useState(() => answersToMap(initialAnswers));
  const [activeCategory, setActiveCategory] = useState("compliance");
  const [saving, setSaving] = useState({}); // question_id -> "saving" | "saved" | "error"
  const [loadingEntity, startLoading] = useTransition();

  const answeredCount = useMemo(
    () => questions.filter((q) => isAnswered(answers[q.id])).length,
    [questions, answers],
  );
  const total = questions.length;
  const pct = total === 0 ? 0 : Math.round((answeredCount / total) * 100);

  function handleEntityChange(nextId) {
    setEntityId(nextId);
    setAnswers({});
    startLoading(async () => {
      const res = await loadAnswersAction(nextId);
      setAnswers(answersToMap(res.answers));
    });
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

  const visible = questions.filter((q) => q.category === activeCategory);

  if (!entities || entities.length === 0) {
    return (
      <p className="text-sm text-text-muted">
        No entities found. Create an entity in the CRM first.
      </p>
    );
  }

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
          <span className="text-xs text-text-muted">Loading answers…</span>
        )}
      </div>

      {/* Progress */}
      <div className="mt-6">
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
          const count = questions.filter((q) => q.category === cat.value).length;
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
          <p className="text-sm text-text-muted">No questions in this category.</p>
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
                  {q.is_required && <span className="ml-1 text-text-muted">*</span>}
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
