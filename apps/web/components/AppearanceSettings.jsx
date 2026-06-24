"use client";

import { useEffect, useState } from "react";

const THEME_STORAGE_KEY = "2ac_theme";

const OPTIONS = [
  { value: "light", label: "Light", description: "Default navy and cream." },
  { value: "warm", label: "Warm", description: "Softer, sand-toned surfaces." },
  {
    value: "high-contrast",
    label: "High Contrast",
    description: "Stronger contrast for readability.",
  },
];

export default function AppearanceSettings() {
  const [theme, setTheme] = useState("light");

  useEffect(() => {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (stored) {
      setTheme(stored);
    }
  }, []);

  function selectTheme(value) {
    setTheme(value);
    window.localStorage.setItem(THEME_STORAGE_KEY, value);
  }

  return (
    <section>
      <h2 className="text-base font-semibold text-ink">Appearance</h2>
      <p className="mt-1 text-sm text-muted">
        Choose how 2nd Act Capital looks for you. Saved to this browser.
      </p>

      <div className="mt-4 grid gap-4 sm:grid-cols-3">
        {OPTIONS.map((option) => {
          const active = theme === option.value;
          return (
            <button
              key={option.value}
              type="button"
              onClick={() => selectTheme(option.value)}
              aria-pressed={active}
              className={`rounded-md border p-4 text-left transition-colors ${
                active
                  ? "border-gold bg-gold-light"
                  : "border-line bg-surface hover:bg-sand"
              }`}
            >
              <span className="block text-sm font-semibold text-ink">
                {option.label}
              </span>
              <span className="mt-1 block text-xs text-muted">
                {option.description}
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
