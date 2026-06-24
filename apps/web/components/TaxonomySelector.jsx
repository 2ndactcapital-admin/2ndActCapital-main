"use client";

import { useState } from "react";
import { useTaxonomy } from "@/lib/useTaxonomy";

const SELECT =
  "mt-1 w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy disabled:opacity-50";
const LABEL =
  "block text-xs font-medium uppercase tracking-wide text-text-muted";

/**
 * Three-level cascading asset taxonomy selector.
 * Stores config_keys (e.g. taxonomy_sc_1) in the form fields, not labels.
 *
 * Props:
 *   initialTaxonomy  — server-fetched taxonomy to avoid loading flash
 *   defaultSuperClass / defaultClass / defaultSubCategory — for edit forms
 */
export default function TaxonomySelector({
  defaultSuperClass = "",
  defaultClass = "",
  defaultSubCategory = "",
  initialTaxonomy = null,
}) {
  const { taxonomy: hookTaxonomy } = useTaxonomy();
  const taxonomy = initialTaxonomy || hookTaxonomy;
  const superClasses = taxonomy.super_classes || [];

  const [superClass, setSuperClass] = useState(defaultSuperClass);
  const [assetClass, setAssetClass] = useState(defaultClass);
  const [subCategory, setSubCategory] = useState(defaultSubCategory);

  const majorClasses = superClass
    ? (superClasses.find((s) => s.key === superClass)?.major_classes || [])
    : [];

  const subCategories = assetClass
    ? (majorClasses.find((m) => m.key === assetClass)?.sub_categories || [])
    : [];

  function onSuperClassChange(e) {
    setSuperClass(e.target.value);
    setAssetClass("");
    setSubCategory("");
  }

  function onAssetClassChange(e) {
    setAssetClass(e.target.value);
    setSubCategory("");
  }

  return (
    <div className="grid gap-4 sm:grid-cols-3">
      <div>
        <label className={LABEL}>Asset super-class</label>
        <select
          name="asset_super_class"
          value={superClass}
          onChange={onSuperClassChange}
          className={SELECT}
        >
          <option value="">Select…</option>
          {superClasses.map((sc) => (
            <option key={sc.key} value={sc.key}>
              {sc.label}
            </option>
          ))}
        </select>
      </div>

      <div>
        <label className={LABEL}>Asset class</label>
        <select
          name="asset_class"
          value={assetClass}
          onChange={onAssetClassChange}
          className={SELECT}
          disabled={!superClass}
        >
          <option value="">Select…</option>
          {majorClasses.map((mc) => (
            <option key={mc.key} value={mc.key}>
              {mc.label}
            </option>
          ))}
        </select>
      </div>

      <div>
        <label className={LABEL}>Sub-category</label>
        <select
          name="asset_sub_category"
          value={subCategory}
          onChange={(e) => setSubCategory(e.target.value)}
          className={SELECT}
          disabled={!assetClass}
        >
          <option value="">Select…</option>
          {subCategories.map((sub) => (
            <option key={sub.key} value={sub.key}>
              {sub.label}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
