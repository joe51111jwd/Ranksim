# Editor plan (workstream 5, branch `claude/rs-editor`)

James: "When I put in a URL I should see the preview. I can do edits even if I don't own it. You just need to own it
to push it." This plan turns the current preview editor into one he can hand to a client.

## The core idea: three copies of a page

| Name | What it is | Changes? |
| --- | --- | --- |
| `source` | The page HTML exactly as fetched. Scripts, styles, comments, odd markup. | Never. |
| `base` | `source` with a `data-rs-id="N"` on every start tag, parsed by the browser and cleaned (scripts out, streamed React content settled), then serialized. Elements the parser invented (an implied `tbody`) get `data-rs-id="xN"`. | Never. |
| `draft` | `base` after the user's edits. Same ids. | Every edit. Undo and redo move between drafts. |

Export (download, copy, publish) is `exportHtml(source, base, draft)`. It walks the source and copies every byte of
it, except where the draft differs from the base:

- An element whose attributes or tag changed gets a rewritten start tag. Untouched attributes keep their exact
  source text, attributes the cleaner hid (`onclick`, `javascript:` links) are kept.
- An element whose child list changed (text edited, block inserted, removed or moved) is merged: children are
  aligned by id and text (longest common subsequence). Hidden source children (scripts, `noscript`, iframes,
  comments, whitespace) stay where they were. New content comes from the draft.
- Moved elements are copied from the source with everything inside them, scripts included.
- Nothing `data-rs*` ever reaches the export.

So an untouched page exports byte for byte, and a page with one edited heading differs in that heading only. The
source is parsed by our own small tokenizer (`roundtrip.js`) that records byte offsets; base and draft are browser
serializations, so a strict parse of them matches the DOM exactly. This keeps the hard part pure and testable with
`node --test`.

HTML mode shows the clean draft without ids. When the user leaves HTML mode (or types), `reattachIds` aligns the
edited elements with the previous draft (exact signature first, then tag only) and puts the ids back. If alignment
is poor the export is still correct; it just rewrites more of the page than needed.

## Modules (`src/aeo/ranksim/static/js/editor/`)

- `roundtrip.js`: `parse`, `annotate`, `exportHtml`, `reattachIds`, `stripRs`, entity decoding. Pure.
- `history.js`: undo and redo over draft strings with typing coalescing and a size cap. Pure.
- `diff.js`: block by block diff of base vs draft with plain labels ("Heading changed", "Paragraph added") and a
  word diff inside changed blocks. Pure.
- `blocks.js`: insertable blocks (section with H2, paragraph, list, table, FAQ with optional FAQPage JSON-LD, image
  with alt), FAQ JSON-LD builder, heading outline, section ranges for drag to move. Works on the DOM API subset that
  `roundtrip.js` lite nodes also implement, so it tests in node.
- `editor.js`, `preview.js` (after P0): wiring. Preview transforms that differ from the page (base href, removed
  `crossorigin`, preload links, lazy images) are stashed in `data-rs-a` and reverted before a draft is serialized, so
  they never count as edits.

## Server: `plugins/drafts.py`

Named versions per page under `project.dir / "plugins" / "drafts"`. Routes (all require sign in; writes need the
CSRF header; refused in the public demo):

- `GET /api/v1/projects/{slug}/drafts?url=` list versions (newest first, no bodies).
- `POST /api/v1/projects/{slug}/drafts` `{url, name, source, base, draft}` save, returns the version.
- `GET /api/v1/projects/{slug}/drafts/{id}` load one with its source and base.
- `DELETE /api/v1/projects/{slug}/drafts/{id}` delete.

Sources and bases are stored once per content hash. Caps: 2 MB per field, 30 versions per page (oldest dropped).
Local autosave in `localStorage` keeps the latest draft per page so a refresh never loses work.

## Editor hooks (BUILD_PLAN 4.3)

`registerEditorPanel({order, render(ctx)})`, `registerEditorTool({order, render(ctx)})`, events `editor-ready` and
`draft-changed`. `ctx`:

```
ctx.url                    current page address
ctx.getDraft()             clean draft HTML (no data-rs attributes)
ctx.getExport()            full page with scripts, ready to publish
ctx.setDraft(html)         replace the draft (undoable)
ctx.doc()                  the preview document, or null in HTML mode
ctx.select(selector)       scroll to and highlight an element; returns it or null
ctx.replaceText(selector, text)  set an element's text (undoable); returns true when found
ctx.diff()                 block diff vs the original
```

## Phases

- A (before P0): the pure modules above with node tests, `plugins/drafts.py` with tests.
- B (after P0): wire into `js/editor/editor.js`, `preview.js`, `css/editor.css`: undo and redo keys and buttons,
  outline panel with click to scroll and drag to move, insert menu, element toolbar (paragraph, H2, H3), diff sheet,
  versions panel, autosave, hooks. Playwright: edit, undo, insert FAQ, download, scripts survived.

## Risks

- Parser edge cases (misnested tags, foreign content). Mitigation: fidelity is by construction for untouched
  regions; property test that an untouched export equals the source on real pages.
- Very large pages: LCS is per changed parent, trimmed to the changed middle, with a capped fallback.

## Verification so far (phase A)

- Untouched export is byte for byte on 18 real pages (SmartCrew, Mitti, HCSS, Wikipedia, MDN, BBC, Stripe, Vercel,
  NYT, Apple, WordPress, Shopify, Webflow, Procore and others), in Chromium with the app cleaner.
- Random edit fuzz in Chromium (text, attributes, remove, insert, move, rename, title) on those pages: 216 trials per
  seed, four seeds, every export reads back as exactly the draft's content with all scripts kept.
- An adversarial review found 22 real bugs, all fixed with tests (`tests/js/editor/browser-cases.test.mjs`, bases
  made by Chromium): document.write scripts with nested script tags, head `<noscript>` tracking iframes, foster
  parented table text, text after `</body>`, nested buttons, implied empty cells, hidden `javascript:` links,
  attributes without spaces, streamed React text, stray `</p>`, pasted copies, HTML mode moves and deletes, quirks
  mode doctypes, draft store races and ordering, FAQ answers typed as bare text, insertion point, svg headings.

## Known gaps

- Misnested legacy formatting (`<b>1<p>2</b>3</p>`): editing the text after the misnest keeps the words but they
  can pick up the bold or link of the copied formatting element.
- Attribute edits on an `<html>` or `<body>` that the page never wrote (browser invented) are not exported.
- Editing the parent of a React streaming boundary moves the streamed content into place in the export.

## Phase B (integrated)

- `js/editor/editor.js`: the round trip state lives in `draft.rs` ({source, base, text, history}); `draft.text` stays
  the clean page for panels, plugins and Test. `normalize()` remembers the HTML each clean page came from, so the
  open flow in app.js needed no change. The preview shows `rs.text`, so every element carries its `data-rs-id`.
- Undo and redo: one history per page for visual typing (coalesced), HTML mode, title and description, inserted
  blocks, block switches and section moves. Cmd or Ctrl Z, Shift Z (and Ctrl Y) in the preview, the HTML view and
  the page; the browser's own designMode undo is intercepted so it never desyncs. Undo updates the preview in place
  (body swapped, scroll kept) instead of reloading it.
- Autosave: IndexedDB keeps the latest draft per page with its own copy of the page, so a refresh restores edits even
  when the site sends different HTML each time (Next.js pages do).
- `js/editor/tools.js`: toolbar (undo, redo; Text, H2, H3 for the block at the cursor; Insert menu: section,
  paragraph, list, table, FAQ with optional FAQPage JSON-LD that follows edits, image with required alt text and an
  http(s) address) and panels (Changes with a plain words summary, a block by block review sheet and Download page;
  Outline with click to scroll and drag to move a section; Versions to save, open and delete named versions).
- `preview.js`: JSON-LD is kept by the cleaner; preview-only link and lazy image changes are stashed and restored,
  so they never count as edits. The sandbox is still `allow-same-origin` only.
- ctx additions (documented in hooks.js): `exportHtml()`, `diff()`, `undo()`, `redo()`, `canUndo`, `canRedo`, event
  `editor-caret`, draft-changed sources `undo`, `redo`, `restore`.
- Tests: `tests/test_editor_e2e.py` runs `tests/js/editor/e2e_editor.cjs` (offline fixture page, real Chromium):
  byte for byte untouched export, typing, undo and redo, FAQ insert, download keeps every script, comment, noscript,
  handler and entity. Manual Playwright runs on SmartCrew and the Mitti HCSS alternative page also covered HTML mode
  undo, block switch, outline drag, saved versions and refresh restore (all passing).
