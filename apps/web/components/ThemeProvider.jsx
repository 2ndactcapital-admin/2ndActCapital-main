"use client";

import { createContext, useContext, useMemo } from "react";

import {
  brandName,
  brandShortName,
  faviconUrl,
  logoUrl,
} from "@/lib/theme";

/**
 * Sprint 24 — client-side access to the tenant's brand.
 *
 * The colours themselves are already applied as `--2a-*` custom properties by
 * the root layout, so components style with `var(--2a-navy)` and never need
 * this context for colour. Use it for the values CSS cannot express: the brand
 * name, the logo URL, per-tenant vocabulary ("Member" / "Deal"), and currency.
 */

const ThemeContext = createContext({ settings: {}, org: {} });

export default function ThemeProvider({ theme, children }) {
  const value = useMemo(() => {
    const settings = theme?.settings || {};
    return {
      settings,
      org: {
        id: theme?.org_id || null,
        name: theme?.org_name || null,
        slug: theme?.org_slug || null,
      },
      brand: {
        name: brandName(settings),
        shortName: brandShortName(settings),
        logoUrl: logoUrl(settings),
        faviconUrl: faviconUrl(settings),
      },
      naming: {
        member: settings["naming.member_label"] || "Member",
        deal: settings["naming.deal_label"] || "Deal",
      },
      footer: {
        privacyUrl: settings["footer.privacy_url"] || null,
        termsUrl: settings["footer.terms_url"] || null,
        supportEmail: settings["footer.support_email"] || null,
      },
      locale: {
        baseCurrency: settings["locale.base_currency"] || "USD",
      },
      get: (key) => settings[key],
    };
  }, [theme]);

  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}

export function useTheme() {
  return useContext(ThemeContext);
}

/** Shorthand for the common case: `const brand = useBrand()`. */
export function useBrand() {
  return useContext(ThemeContext).brand;
}
