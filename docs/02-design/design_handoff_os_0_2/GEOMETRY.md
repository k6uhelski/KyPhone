# KyPhone OS 0.2.1 — geometry

Literal values as written in the prototype markup (`KyPhone UI v4.dc.html`).
Panel is 600x600. All offsets are from the panel's top-left. "Rule" means a
horizontal divider; its thickness is its `border-top` width.

Selection everywhere is inversion: background #111111, foreground #FFFFFF.
Unselected is background transparent, foreground #111111.

## Shared header (every list screen)

| Element | Geometry |
| --- | --- |
| Header strip | top 0, height 44, padding 0 16 (thread: height 46) |
| Back control | 38 x 34 box, centered glyph `<`, 24px bold |
| Screen name | centered, 24px bold |
| Right control | 38 x 34 box, 24px bold (`+` or `i`); EDIT is height 34, padding 0 10, 18px bold |
| Rule under header | top 43, 1px (thread and compose: top 44/46, 2px) |

## Footer strip (contacts only)

Texts and calls have no footer: their rows run to the bottom edge of the panel.

| Element | Geometry |
| --- | --- |
| Rule | bottom 45, 2px |
| Strip | bottom 0, height 45, padding 0 28, space-between |
| Left group | `LOOK UP:` 18px + query 24px + 18 x 24 block cursor, gap 14 |
| Right | 18px, `3 / 14` |

## Texts list — CHANGED

5 rows windowed, filling the panel. Row i (0-4) top = 44 + i*111.

| Element | Geometry |
| --- | --- |
| Row | left 0 right 0, height 111, padding 22 28, border-bottom 1px, overflow hidden |
| Name | 24px, weight 700 unread / 500 read, capped 14 chars |
| Time + chevron | 18px, gap 10, baseline-aligned, right |
| Preview | 18px, margin-top 18, nowrap, capped 24 chars |
| Preview prefix | `You: ` outgoing, `! ` outgoing and not sent |
| Empty state | top 44, left/right 28, bottom 0, centered column, gap 14; 24px bold title over 18px hint |

## Thread — CHANGED

| Element | Geometry |
| --- | --- |
| Header | top 0, height 46; rule top 46, 2px |
| Message area | top 62, left/right 16, bottom = composerH + 17, column, justify flex-end, gap 16 |
| Bubble | max-width 400, border 2px, padding 8 12; text 24px, line-height 1.45 |
| Bubble fill | ink #111 only when state is `sent`; otherwise paper |
| Selected bubble | box-shadow inset 0 0 0 3px #fff, inset 0 0 0 5px #111 — inside the border box, so it never meets the tail |
| Tail | 5 stacked bars, heights 4, widths 4/8/14/8/4, on the bubble's outer edge |
| Sender name | 18px bold, margin 0 14 6 14, incoming only |
| Meta label | 18px, margin 6 14 0 14; weight 700 when NOT SENT. ASCII only: `SENDING...`, `SENT 6:53 PM`, `NOT SENT`, `NOT SENT - ENTER TO RETRY` |
| Composer rule | bottom = composerH, 2px |
| Composer | bottom 0, height composerH, padding 8 24 0 24, align flex-start, gap 10 |
| composerH | 45 (1 line), 79 (2), 113 (3) |
| Draft text | 24px, line-height 34, flex 1, word-break break-word |
| Cursor | 18 x 24 ink block, vertical-align text-bottom |

## Compose — CHANGED

| Element | Geometry |
| --- | --- |
| Title `NEW MESSAGE` | top 10, left 24, 24px bold |
| X control | top 8, right 16, 38 x 34 |
| Rule | top 44, 2px |
| `TO:` label | top 56, left 24, 18px, padding 2 4 |
| TO value | top 84, left/right 24, 24px; `+` control 38 x 34 at right when TO is empty |
| Rule | top 122, 1px |
| `MESSAGE:` label | top 134, left 24, 18px, padding 2 4 |
| Message value | top 162, left/right 24, 24px, line-height 34, word-break break-word |
| SEND | bottom 14, right 24, border 3px, padding 6 18, 18px bold |

## Contact page — CHANGED

| Element | Geometry |
| --- | --- |
| Header | top 0, height 44; EDIT right when saved, empty 38 x 34 when unsaved |
| Rule | top 43, 1px |
| Title | top 150, left/right 28, 48px bold (name, or formatted number when unsaved) |
| Sub-line | margin-top 18, 24px (number, `NOT IN CONTACTS`, or `NO NUMBER SAVED`) |
| Rule | top 330, left/right 28, 2px |
| Action row | top 360, left/right 28, flex, gap 16 |
| Button | border 3px, padding 8 22, 24px bold |
| Actions, saved with number | CALL, TEXT |
| Actions, unsaved | CALL, TEXT, SAVE |
| Actions, saved without number | ADD NUMBER |

## New / edit contact — CHANGED

Identical geometry; the title and the DELETE button differ.

| Element | Geometry |
| --- | --- |
| Header | top 0, height 44; X left 38 x 34; title `NEW CONTACT` or `EDIT CONTACT`; right slot empty 38 x 34 |
| Rule | top 43, 1px |
| `FIRST NAME:` | top 70, left 24, 18px, padding 2 4 |
| First value | top 100, left/right 24, 24px |
| Rule | top 138, 1px |
| `LAST NAME:` | top 158, left 24, 18px, padding 2 4 |
| Last value | top 188, left/right 24, 24px |
| Rule | top 226, 1px |
| `PHONE NUMBER:` | top 246, left 24, 18px, padding 2 4 |
| Number value | top 276, left/right 24, 24px |
| Rule | top 314, 1px |
| DELETE | bottom 15, left 24, border 2px, padding 7 18, 18px; edit only |
| SAVE | bottom 14, right 24, border 3px, padding 6 18, 18px bold |
| Field cursor | 18 x 24 ink block after the active field's value |
| Active field label | inverted |

## Confirmation (discard message, delete contact) — CHANGED

| Element | Geometry |
| --- | --- |
| Title | top 10, left 24, 24px bold |
| Rule | top 44, 2px |
| Body block | top 196, left/right 56, flex, gap 22, align flex-start |
| Icon | 46 x 46, border 2px, centered `!`, 24px bold |
| Body text | 24px, line-height 34 |
| Button row | bottom 24, left 56, right 24, space-between |
| Destructive (left) | border 2px, padding 7 18, 18px |
| Safe default (right) | border 3px, padding 6 18, 18px bold, selected on open |

## Stop alert (unbuilt feature, validation, empty send) — UNCHANGED geometry

| Element | Geometry |
| --- | --- |
| Title | top 10, left 24, 24px bold |
| Rule | top 44, 2px |
| Body block | top 212, left/right 56, flex, gap 22 |
| Icon | 46 x 46, border 2px, centered `!`, 24px bold |
| Body text | 24px, line-height 34 |
| OK | bottom 24, right 24, border 3px, padding 6 18, 18px bold, inverted (it is the only control) |

## Contacts list — CHANGED

7 rows windowed. Row i (0-6) top = 44 + i*64.

| Element | Geometry |
| --- | --- |
| Row | height 64, padding 0 28, border-bottom 1px, space-between |
| Name | 24px weight 600, capped 18 chars |
| Number | 18px, formatted, or `NO NUMBER` |

## Calls list — CHANGED

6 rows windowed, filling the panel. Row i (0-5) top = 44 + i*92. Seven rows would cost 266 characters, over budget.

| Element | Geometry |
| --- | --- |
| Row | height 92, padding 0 28, border-bottom 1px, space-between |
| Name | 24px weight 600, capped 14 chars |
| Tag | 18px, border 1px currentColor, padding 0 6, gap 12 from name |
| Time / duration | right, 18px, line-height 22, stacked |

## Home menu — CHANGED (order only)

| Element | Geometry |
| --- | --- |
| Status bar | top 0, height 60, padding 0 24, space-between |
| Rule | top 60, 2px |
| Menu viewport | top 62, bottom 0, overflow hidden; usable height 532 |
| Row | height 135, centered, gap 28, border-bottom 1px |
| Order | TEXT, CALL, CONTACTS, READ, LISTEN |
| Icon | 56 x 56 SVG on a 24 x 24 grid, 1px outline weight |
| Label | 48px bold |
| Unread badge | absolute left 100%, margin-left 24, vertically centered, 18px |
| Scroll shift | translate menu by -max(0, (index+1)*135 - 532) |
| More-below cue | bottom 6, right 12, three stacked bars 14/8/3 x 3px, gap 2 |

## Lock screen — UNCHANGED

| Element | Geometry |
| --- | --- |
| OS version | bottom 8, left 10, 18px weight 600 |
| Clock | top 159, centered, 64px bold |
| Date | top 243, centered, 24px |
| Quote block | top 356, left/right 60, centered; quote 24px line-height 34, attribution 18px margin-top 12 |
| Cat bitmap | bottom 6, right 8, fixed 1-bit bitmap |
