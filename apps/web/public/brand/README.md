# 2nd Act Capital — Brand Assets

Drop these into `apps/web/public/`. Open **2nd Act Capital — Brand Style Tile.dc.html** for the full visual reference.

## Tokens
- Navy `#1B2B4B` · Gold `#C5A880` · Gold Light `#E8D5A3` · Background `#FAF9F6` · Text `#0F172A`
- Nav-on-navy resting icon: `#9AA6BF`
- `tokens.css` ships these as `--2a-*` custom properties.

## Type
- **Display / headings:** Spectral (Google Fonts) — 300/400/500/600 + italic
- **Body / UI:** Hanken Grotesk (Google Fonts) — 400/500/600/700, base **17px**
```html
<link href="https://fonts.googleapis.com/css2?family=Spectral:ital,wght@0,300;0,400;0,500;0,600;1,400&family=Hanken+Grotesk:wght@400;500;600;700&display=swap" rel="stylesheet">
```

## Icon mark — "The Ascent"
- `icon/app-icon.svg` — gold on navy, primary (512, use for PWA/app icon)
- `icon/app-icon-light.svg` — navy on cream, reversed
- `icon/favicon.svg` — favicon-optimized (bolder stones, reads at 16px)
- `icon/avatar.svg` — circular crop for member profiles
- `icon/mark-navy.svg` / `icon/mark-gold.svg` — transparent, single-colour

## Wordmark
- `wordmark/wordmark-cream-bg.svg` / `wordmark-navy-bg.svg`
- For crispest rendering prefer **live text** — see the `.wordmark` lockup in `tokens.css`.

## Nav icons (`nav-icons/`, 24px grid, `stroke="currentColor"`)
dashboard · marketplace · portfolio · portfolio-reporting · spv-manager · investment-profile · insurance · community · admin
Resting `#9AA6BF` on navy → active `#E8D5A3`; bump stroke to 1.7 at 20px.
