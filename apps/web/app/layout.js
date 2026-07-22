import "./globals.css";

import ThemeProvider from "@/components/ThemeProvider";
import {
  brandName,
  faviconUrl,
  fontHref,
  loadTheme,
  themeToCssVars,
} from "@/lib/theme";

// Sprint 24: title, icons and the entire palette come from the tenant's
// org_settings. Nothing here is hardcoded to a particular client.
export async function generateMetadata() {
  const theme = await loadTheme();
  const settings = theme.settings || {};
  const name = brandName(settings);
  const favicon = faviconUrl(settings);

  return {
    title: name || undefined,
    description: settings["brand.tagline"] || undefined,
    ...(favicon ? { icons: { icon: favicon, apple: favicon } } : {}),
    manifest: "/manifest.webmanifest",
  };
}

export default async function RootLayout({ children }) {
  const theme = await loadTheme();
  const settings = theme.settings || {};

  // Rendered into <head> so the tenant's palette is present on first paint —
  // a client-side provider would flash an unbranded frame first.
  const cssVars = themeToCssVars(settings);
  const fonts = fontHref(settings);

  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        {fonts && <link href={fonts} rel="stylesheet" />}
        {cssVars && (
          <style
            id="tenant-theme"
            dangerouslySetInnerHTML={{ __html: `:root{${cssVars}}` }}
          />
        )}
      </head>
      <body>
        <ThemeProvider theme={theme}>{children}</ThemeProvider>
      </body>
    </html>
  );
}
