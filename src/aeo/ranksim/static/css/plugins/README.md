# Studio plugin styles

Every `*.css` file in this folder is added to the Studio shell automatically as a stylesheet, sorted by file name,
after `css/base.css` and the area files (`css/home.css`, `css/editor.css` and so on). Adding a file is enough.

## Contract

- One file per feature, with the same name as its script: `js/plugins/gsc.js` pairs with `css/plugins/gsc.css`.
- File names use letters, digits, `.`, `_` and `-` only, and must not start with a dot. Other names are not served.
- Prefix every class with the feature name (`.gsc-chart`, `.history-line`) so plugins never restyle each other or
  the core screens. Reuse the shared components (`.tile`, `.bento`, `.btn`, `.chip`, `.label`, `.rowcard`, `.field`)
  instead of copying them.
- Use the design tokens from `css/base.css` (`var(--bg)`, `var(--tile)`, `var(--card)`, `var(--ink)`, `var(--ink-2)`,
  `var(--blue)`, `var(--dark)`, `var(--line)`, `var(--r-tile)`, `var(--shadow)` and the rest). They switch for dark
  mode on their own.
- Design language (fixed): warm off-white page, soft gray tiles, white cards, one blue accent, black tiles. Geist and
  Geist Mono only. No serif fonts, no gradients, no glass or blur.
- Every screen must work at phone width with no horizontal scroll. Add `@media (max-width: 720px)` rules in your own
  file.
- Each stylesheet is fetched with a version query string, so a new deploy is picked up on the next page load.
