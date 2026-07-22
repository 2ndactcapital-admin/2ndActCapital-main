import { brandName, brandShortName, loadTheme, logoUrl } from "@/lib/theme";

// Sprint 24: the PWA manifest is generated per tenant. Replaces the static
// public/manifest.json, which hardcoded one client's name and palette.
export default async function manifest() {
  const theme = await loadTheme();
  const settings = theme.settings || {};
  const icon = logoUrl(settings);

  return {
    name: brandName(settings),
    short_name: brandShortName(settings),
    icons: icon
      ? [{ src: icon, sizes: "512x512", type: "image/svg+xml" }]
      : [],
    theme_color: settings["brand.color.navy"],
    background_color: settings["brand.color.bg_app"],
    display: "standalone",
  };
}
