"use client";

import { useEffect, useState } from "react";

const INPUT =
  "mt-1 w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";

// Mapping: country_code → list_key for region dropdown.
// Only include list_keys that are seeded in the live reference_data table.
// Unmapped countries fall back to a free-text input.
const REGION_LIST = {
  US: "us_state",
  CA: "ca_province",
};

async function fetchItems(listKey, parentCode) {
  const url = parentCode
    ? `/api/reference/${listKey}?parent_code=${encodeURIComponent(parentCode)}`
    : `/api/reference/${listKey}`;
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) return [];
  const data = await res.json();
  return data.items || [];
}

/**
 * Simple reference list select.
 * Fetches from /api/reference/{listKey} on mount.
 */
export function ReferenceSelect({ listKey, name, defaultValue, placeholder = "Select…", className }) {
  const [items, setItems] = useState([]);

  useEffect(() => {
    fetchItems(listKey, null).then(setItems);
  }, [listKey]);

  return (
    <select name={name} defaultValue={defaultValue || ""} className={className || INPUT}>
      <option value="">{placeholder}</option>
      {items.map((it) => (
        <option key={it.code} value={it.code}>
          {it.label}
        </option>
      ))}
    </select>
  );
}

/**
 * Linked country + region selects.
 * Emits hidden inputs named `country_code` and `region_code`.
 * When country = US/CA, shows a dropdown; otherwise shows a text input.
 */
export function CountryRegionSelect({
  defaultCountryCode,
  defaultRegionCode,
  countryName = "country_code",
  regionName = "region_code",
  subdivisionLabel,
}) {
  const [countries, setCountries] = useState([]);
  const [regions, setRegions] = useState([]);
  const [country, setCountry] = useState(defaultCountryCode || "");
  const [region, setRegion] = useState(defaultRegionCode || "");
  const [subdLabel, setSubdLabel] = useState(subdivisionLabel || "State / Region");

  useEffect(() => {
    fetchItems("country", null).then(setCountries);
  }, []);

  useEffect(() => {
    const listKey = REGION_LIST[country];
    if (listKey) {
      fetchItems(listKey, country).then(setRegions);
    } else {
      setRegions([]);
    }
    // Update subdivision label from country extra
  }, [country]);

  function handleCountryChange(e) {
    const val = e.target.value;
    setCountry(val);
    setRegion("");
    // Derive subdivision label from fetched countries list
    const found = countries.find((c) => c.code === val);
    setSubdLabel(found?.extra?.subdivision || "State / Region");
  }

  const regionListKey = REGION_LIST[country] || null;

  return (
    <div className="flex flex-col gap-2 sm:flex-row">
      <div className="flex-1">
        <select
          name={countryName}
          value={country}
          onChange={handleCountryChange}
          className={INPUT}
        >
          <option value="">Country…</option>
          {countries.map((c) => (
            <option key={c.code} value={c.code}>
              {c.label}
            </option>
          ))}
        </select>
      </div>
      {country && (
        <div className="flex-1">
          {regionListKey ? (
            <select
              name={regionName}
              value={region}
              onChange={(e) => setRegion(e.target.value)}
              className={INPUT}
            >
              <option value="">{subdLabel}…</option>
              {regions.map((r) => (
                <option key={r.code} value={r.code}>
                  {r.label}
                </option>
              ))}
            </select>
          ) : (
            <input
              name={regionName}
              value={region}
              onChange={(e) => setRegion(e.target.value)}
              placeholder={subdLabel || "State / Region"}
              className={INPUT}
            />
          )}
        </div>
      )}
    </div>
  );
}
