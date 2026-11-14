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
