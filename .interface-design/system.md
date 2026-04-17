# SignalForge · Interface Design System

Editorial periodical. Cream paper, typewriter ink, wire-service bylines. Every
screen should read like a page from *Issue No. 01* of a private GTM dossier —
not a SaaS dashboard.

## Direction

- **Domain**: dispatches · wire service · dossier · intelligence brief · ledger ·
  byline · filing · editor's note · colophon · issue number.
- **Feel**: serious, analyst, dense but breathing; whisper-quiet layering; one
  accent (carmine) reserved for the top-priority marker.
- **Rejecting**: Streamlit's Source Sans + blue primary · rounded lead cards ·
  fill-colored pill buttons.
- **Signature**: every signal renders as a *dispatch byline* — monospace slug on
  the left (`GREENHOUSE · HIRING · 0.90`), wire-service text on the right,
  stacked under a numbered lead (`— DISPATCH 01 · TOP MATCH —`).

## Tokens

```css
:root {
  --paper:   #f7f3ea;   /* base surface — newsprint cream */
  --paper-2: #efeadf;   /* one-step inset — only for form inputs */
  --paper-3: #e7dfd0;   /* reserved — not used in v1 */

  --ink:     #1a1814;   /* warm black, typewriter */
  --ink-2:   #403a32;   /* secondary text */
  --ink-3:   #827868;   /* tertiary / meta / byline */
  --ink-4:   #a79d8d;   /* placeholder, em-dash prefix */

  --rule:    rgba(26, 24, 20, 0.12);   /* hairline separator */
  --rule-2:  rgba(26, 24, 20, 0.22);   /* emphasis rule */

  --carmine: #9c3324;   /* reserved. Top-lead marker only. */

  --serif: 'Fraunces', Georgia, serif;
  --sans:  'IBM Plex Sans', -apple-system, system-ui, sans-serif;
  --mono:  'IBM Plex Mono', ui-monospace, Menlo, monospace;
}
```

## Depth

**Borders-only.** No shadows. No colored surfaces. A single 3px `--ink`
masthead rule at top; 1px hairlines at 12% ink elsewhere. One level of
inset (input field → `--paper-2`) — that is the only elevation shift in
the system.

## Spacing

Base 4px. Scale: 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64.
Sections separated by **48px top-pad + 1px top-rule**.
Container max-width **780px**.

## Typography

| Role | Font | Weight | Size | Notes |
|---|---|---|---|---|
| Masthead title | Fraunces | 400 | 64px | italic `<em>`, `opsz` 144 |
| Lede | Fraunces | 300 | 19px | `max-width: 54ch` |
| Section title | Fraunces italic | 400 | 28px | |
| Lead company name | Fraunces | 400 | 32px | `opsz` 72 |
| Brief summary | Fraunces | 300 | 22px | |
| Why-block | Fraunces italic | 400 | 14px | `padding-left: 16px`, left-rule |
| Section number | Plex Mono | 400 | 11px | `letter-spacing .14em`, uppercase |
| Byline / meta | Plex Mono | 400 | 10-11px | `letter-spacing .08-.18em`, upper |
| Body | Plex Sans | 400 | 14-15px | `line-height 1.5-1.9` |
| Score / number | Plex Mono | 500 | — | `font-variant-numeric: tabular-nums` |

## Component patterns

### Section head (§ N + italic title)

Flex row, baseline-aligned, 16px gap. `§ NN` in mono uppercase,
min-width 56px, `--ink-3`. Title in italic serif 28px, margin 0.

### Dispatch byline row

```
display: grid;
grid-template-columns: 180px 1fr;
gap: 18px;
border-top: 1px dotted var(--rule);
```

Left cell: mono uppercase byline, `--ink-3`, `tabular-nums`.
Right cell: plex sans body, `--ink`. Anchors are borderless bottom-line
links, hover transitions the rule to `--ink`.

### Brief two-column grid

```
display: grid;
grid-template-columns: 1fr 1fr;
gap: 40px;
border-top: 1px solid var(--rule);
padding-top: 20px;
```

Labels ("Target titles", "Signal weights") in 10px mono uppercase
`--ink-3`. List items use `::before { content: "— "; }` in `--ink-4`.

### Form

Input: `--paper-2` background, 1px `--rule-2` border, 0 radius, 14-16px
padding. Focus transitions border to `--ink`, no glow.
Button: ink-filled rectangle, `--paper` text, 0 radius, 10.5px mono
uppercase, `letter-spacing .14em`. Hover inverts to outlined.

## Streamlit implementation gotchas

1. **Inject the style block every render.** `@st.cache_data` / session-state
   guards do not work — Streamlit rebuilds the DOM on each rerun, so a
   one-time inject disappears after the first submit.
2. **Use `st.html()`, not `st.markdown(..., unsafe_allow_html=True)`.**
   Markdown sanitizes class attributes and breaks structural HTML.
   `st.html()` passes through DOMPurify which preserves classes.
3. **`!important` on aesthetic rules.** Streamlit's own stylesheet wins
   specificity for `h1..h6`, `p`, list items. Without `!important` the
   editorial typography never lands.
4. **`innerText` reflects `text-transform`.** A CSS-uppercased "§ 01"
   returns `"§ 01"` as `textContent` but `"§ 01"` stays upper in
   `innerText`. Test selectors must normalize to `.toLowerCase()`.
5. **Carmine is reserved.** Use only on `.sf-lead-num .carmine` for the
   top-priority dispatch. Never for buttons, links, or body text.
