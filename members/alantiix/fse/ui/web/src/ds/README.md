# Design system (copy)

Copied from `design_system/` (components `.jsx` + `.d.ts`, `components.css`, `tokens/`,
`styles.css`). Do not edit the copies except for the two console changes below; change the
design system and copy again.

Console changes:

1. `components/brand/Icon.jsx` reads bundled SVGs from `icons.js` (lucide-static 0.469.0)
   instead of fetching them from unpkg: no third-party request, works under a strict CSP.
2. `tokens/fonts.css` no longer imports Google Fonts; Geist Mono is self-hosted from
   `@fontsource/geist-mono` (imported in `src/main.tsx`).

`index.js` / `index.d.ts` are the barrel the adherence lint asks for.
