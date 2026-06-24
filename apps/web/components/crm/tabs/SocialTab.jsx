"use client";

import { useActionState, useEffect, useRef, useState } from "react";
import {
  IconBrandLinkedin,
  IconBrandX,
  IconBrandFacebook,
  IconBrandInstagram,
  IconWorld,
} from "@tabler/icons-react";
import { addSocialProfileAction } from "@/lib/crmActions";

const INPUT = "rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";
const PLATFORMS = [
  { value: "linkedin", label: "LinkedIn" },
  { value: "twitter", label: "Twitter / X" },
  { value: "facebook", label: "Facebook" },
  { value: "instagram", label: "Instagram" },
  { value: "angellist", label: "AngelList" },
  { value: "crunchbase", label: "Crunchbase" },
  { value: "other", label: "Other" },
];

function platformIcon(platform) {
  const map = {
    linkedin: IconBrandLinkedin,
    twitter: IconBrandX,
    facebook: IconBrandFacebook,
    instagram: IconBrandInstagram,
  };
  const Icon = map[platform] || IconWorld;
  return <Icon size={18} className="text-navy" />;
}

function platformLabel(v) {
  return PLATFORMS.find((p) => p.value === v)?.label || v;
}

export default function SocialTab({ entityId, initial }) {
  const [items, setItems] = useState(initial || []);
  const [adding, setAdding] = useState(false);
  const formRef = useRef(null);
  const [state, formAction, pending] = useActionState(
    addSocialProfileAction.bind(null, entityId),
    {},
  );

  useEffect(() => {
    if (state?.ok && state.item) {
      setItems((prev) => {
        const without = prev.filter((s) => s.id !== state.item.id && s.platform !== state.item.platform);
        return [...without, state.item];
      });
      formRef.current?.reset();
      setAdding(false);
    }
  }, [state]);

  return (
    <div>
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-navy">Social Profiles</h2>
        {!adding && (
          <button type="button" onClick={() => setAdding(true)} className="text-sm font-medium text-navy hover:underline">
            Add profile
          </button>
        )}
      </div>

      {items.length === 0 ? (
        <p className="mt-3 text-sm text-text-muted">No social profiles.</p>
      ) : (
        <ul className="mt-3 space-y-2">
          {items.map((s) => (
            <li key={s.id} className="flex items-center gap-3 rounded-lg border border-border bg-bg-card p-3">
              {platformIcon(s.platform)}
              <div className="min-w-0 flex-1">
                <a href={s.url} target="_blank" rel="noreferrer" className="block truncate text-sm text-navy hover:underline">
                  {s.url}
                </a>
                {s.platform === "linkedin" && (
                  <span className="text-xs text-text-muted">Import stub — coming soon</span>
                )}
              </div>
              <span className="text-xs text-text-muted">{platformLabel(s.platform)}</span>
            </li>
          ))}
        </ul>
      )}

      {adding && (
        <form ref={formRef} action={formAction} className="mt-4 grid max-w-xl gap-3 sm:grid-cols-2">
          <select name="platform" defaultValue="linkedin" className={INPUT}>
            {PLATFORMS.map((p) => (
              <option key={p.value} value={p.value}>{p.label}</option>
            ))}
          </select>
          <div />
          <input name="url" placeholder="Profile URL *" required className={`${INPUT} sm:col-span-2`} />
          <label className="flex items-center gap-2 text-sm text-text-secondary">
            <input type="checkbox" name="is_primary" /> Primary
          </label>
          {state?.error && <p className="text-sm text-[#9B2335] sm:col-span-2">{state.error}</p>}
          <div className="flex gap-2 sm:col-span-2">
            <button type="submit" disabled={pending} className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60">
              {pending ? "Saving…" : "Save profile"}
            </button>
            <button type="button" onClick={() => setAdding(false)} className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text-secondary hover:bg-border">
              Cancel
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
