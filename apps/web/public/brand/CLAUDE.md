# 2nd Act Capital — Brand & UI Conventions

This file defines the visual system for the 2nd Act Capital web app. Follow it for all UI work. Brand assets live in `apps/web/public/brand/`.

## Product register
Private membership platform for post-liquidity founders/operators. Premium private club, **not** a fintech startup — Soho House meets a boutique family office. Understated luxury, discretion, earned trust. Light/cream UI only — **no dark mode**.

## Design tokens
Import `public/brand/tokens.css` (or copy into your token layer):

```css
:root{
  --2a-navy:#1B2B4B;        /* structure, headings, nav, dark panels */
  --2a-gold:#C5A880;        /* accent: marks, emphasis, hairline rules */
  --2a-gold-light:#E8D5A3;  /* highlight: on-navy accent, active/hover */
  --2a-bg:#FAF9F6;          /* warm cream canvas — app background */
  --2a-text:#0F172A;        /* body ink */
  --2a-nav-rest:#9AA6BF;    /* nav icon on navy, resting */
}
```
Never introduce colors outside this set. No bright greens, no gradients.

## Typography
Load once in the document `<head>`:
```html
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Spectral:ital,wght@0,300;0,400;0,500;0,600;1,400&family=Hanken+Grotesk:wght@400;500;600;700&display=swap" rel="stylesheet">
```
- **Display / headings:** `'Spectral', Georgia, serif` (300–600 + italic)
- **Body / UI:** `'Hanken Grotesk', system-ui, sans-serif`, **base 17px**
- Eyebrows/labels: Hanken 600–700, uppercase, ~0.22em tracking, gold.

## Logo & marks (`public/brand/`)
- `icon/app-icon.svg` — gold-on-navy seal, primary (PWA/app icon, 512)
- `icon/app-icon-light.svg` — navy on cream, reversed
- `icon/favicon.svg` — favicon-optimized (reads at 16px)
- `icon/avatar.svg` — circular crop for member profiles
- `icon/mark-navy.svg` / `icon/mark-gold.svg` — single-color, transparent
- `wordmark/wordmark-cream-bg.svg`, `wordmark/wordmark-navy-bg.svg` — for crispest text prefer the live-text `.wordmark` lockup in `tokens.css`.

The mark is "The Ascent": three rounded squares rising on a diagonal, top one gold-light.

## Navigation icons (`public/brand/nav-icons/`, `stroke="currentColor"`)
`dashboard · marketplace · portfolio · portfolio-reporting · spv-manager · investment-profile · insurance · community · admin`
- 24px grid, 1.6px strokes (bump to 1.7 at 20px), rounded caps/joins.
- On navy sidebar: resting `var(--2a-nav-rest)` → active `var(--2a-gold-light)`, with a soft gold wash (`rgba(232,213,163,.12)`) behind the active item; active label in cream.
- SPV Manager is a sealed legal document (SPV = contract/legal entity), used for entity mapping.

## Patterns
- App background is cream; cards are white `#fff` with a `1px solid #ece8dd` hairline and `6px` radius — **no** heavy shadows, no left-accent-border cards.
- Hairline rules and emphasis in gold; primary actions in navy.
- Copy voice: quiet, precise, no hype. *Members / allocation / co-invest / discretion*, never *users / unlock / supercharge*. No emoji.

## Avoid
Fintech aesthetics, gradients, heavy law-firm serifs, dollar-sign/bar-chart iconography, dark mode.
