"use client";

import { useState, useEffect } from "react";

let _cache = null;
let _promise = null;

async function fetchTaxonomy() {
  if (_cache) return _cache;
  if (!_promise) {
    _promise = fetch("/api/taxonomy")
      .then((r) => r.json())
      .then((data) => {
        _cache = data;
        _promise = null;
        return data;
      })
      .catch(() => {
        _promise = null;
        return { super_classes: [] };
      });
  }
  return _promise;
}

export function useTaxonomy() {
  const [taxonomy, setTaxonomy] = useState(_cache || { super_classes: [] });
  const [loading, setLoading] = useState(!_cache);

  useEffect(() => {
    if (_cache) {
      setTaxonomy(_cache);
      setLoading(false);
      return;
    }
    setLoading(true);
    fetchTaxonomy().then((data) => {
      setTaxonomy(data);
      setLoading(false);
    });
  }, []);

  function getMajorClasses(superClassKey) {
    if (!superClassKey) return [];
    const sc = taxonomy.super_classes?.find((s) => s.key === superClassKey);
    return sc?.major_classes || [];
  }

  function getSubCategories(majorClassKey) {
    if (!majorClassKey) return [];
    for (const sc of taxonomy.super_classes || []) {
      const mc = sc.major_classes?.find((m) => m.key === majorClassKey);
      if (mc) return mc.sub_categories || [];
    }
    return [];
  }

  return { taxonomy, loading, getMajorClasses, getSubCategories };
}
