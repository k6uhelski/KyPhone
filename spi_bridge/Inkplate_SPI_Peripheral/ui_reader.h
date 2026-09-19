// ui_reader.h — the book reader's page frames, drawn with the FreeSerif fonts.
//
// A page of book text arrives as several commands, and the panel only refreshes on the last:
//
//   RTEXT|size|row|S/-|line·line·...   Draw lines from row `row` in font `size` (S M L X, FreeSerif 9/12/18/24pt).
//                                      `S` marks the first frame of a page: start from a blank screen.
//                                      Never refreshes.
//   RFOOT|P/F|left|right               Draw the footer (a rule, `left` at the left margin, `right` at the right)
//                                      and refresh: P partial, F full.
//
// The Radxa has already wrapped every line to the column (reader_layout.py) using the same glyph advances the panel
// sums as it prints, so nothing here decides what fits. The geometry below MUST match reader_layout.py:
//   text column x = READER_X .. READER_X + READER_W; row r sits on baseline READER_TOP + r*yAdvance + (3*yAdvance)/4
//   footer: a 1px rule at READER_RULE_Y, then text in the built-in font (size 2) on baseline READER_FOOT_BASE.
//
// Like ui_screens.h this depends only on a global `display` (setFont / setTextWrap / setCursor / setTextSize /
// setTextColor / print / fillRect / clearDisplay), so spi_bridge/tests/firmware_host/ builds it against a fake display.

#ifndef KYPHONE_UI_READER_H
#define KYPHONE_UI_READER_H

#include "ui_screens.h"
#include "fonts/FreeSerif9pt7b.h"
#include "fonts/FreeSerif12pt7b.h"
#include "fonts/FreeSerif18pt7b.h"
#include "fonts/FreeSerif24pt7b.h"

#define READER_X          32
#define READER_W          536
#define READER_TOP        24
#define READER_RULE_Y     564
#define READER_FOOT_BASE  590
#define READER_MAX_ROW    63        // rows past this are ignored (a page has at most 24)

enum { UI_READER_NONE = 0, UI_READER_PARTIAL = 1, UI_READER_FULL = 2 };

static const GFXfont* ui_reader_font(char size) {
    switch (size) {
        case 'S': return &FreeSerif9pt7b;
        case 'M': return &FreeSerif12pt7b;
        case 'L': return &FreeSerif18pt7b;
        case 'X': return &FreeSerif24pt7b;
    }
    return NULL;
}

static bool ui_is_reader_frame(const char* text) {
    return strncmp(text, "RTEXT|", 6) == 0 || strncmp(text, "RFOOT|", 6) == 0;
}

// RTEXT|size|row|flag|line·line·...
static void ui_rtext(char* data) {
    char* f[4];
    int n = ui_split(data, '|', f, 4);
    if (n < 4) return;
    const GFXfont* font = ui_reader_font(f[0][0]);
    if (!font || f[0][1] != '\0' || f[1][0] == '\0') return;
    int row = 0;                                                            // digits only: anything else is a garbled frame
    for (const char* d = f[1]; *d; d++) {
        if (*d < '0' || *d > '9') return;
        row = row * 10 + (*d - '0');
        if (row > READER_MAX_ROW) return;
    }
    if (f[2][0] == 'S') display.clearDisplay();

    display.setFont(font);
    display.setTextSize(1);
    display.setTextColor(BLACK);
    display.setTextWrap(false);
    int y_adv = font->yAdvance;
    for (char* p = f[3]; ; row++) {
        char* end = strchr(p, UI_SUB);
        if (end) *end = '\0';
        int baseline = READER_TOP + row * y_adv + (3 * y_adv) / 4;
        if (baseline - y_adv > 600 || row > READER_MAX_ROW) break;          // below the panel: nothing more to draw
        if (*p) {
            display.setCursor(READER_X, baseline);                          // with a custom font the cursor y is the baseline
            display.print(p);
        }
        if (!end) break;
        p = end + 1;
    }
    display.setFont(NULL);                                                  // back to the built-in font for every other screen
    display.setTextWrap(true);
}

// RFOOT|P/F|left|right — draws the footer; the return value says how to refresh.
static int ui_rfoot(char* data) {
    char* f[3];
    int n = ui_split(data, '|', f, 3);
    display.setFont(NULL);
    display.fillRect(READER_X, READER_RULE_Y, READER_W, 1, BLACK);
    ui_text(ui_fld(f, n, 1), READER_X, READER_FOOT_BASE, 2, BLACK, false);
    ui_text_right(ui_fld(f, n, 2), READER_X + READER_W, READER_FOOT_BASE, 2, BLACK, false);
    return f[0][0] == 'F' ? UI_READER_FULL : UI_READER_PARTIAL;
}

// Draws a reader frame and returns what the panel should do: UI_READER_NONE (a text frame: nothing to show yet),
// UI_READER_PARTIAL or UI_READER_FULL (the page is complete: refresh). `text` is modified in place.
static int ui_reader_frame(char* text) {
    if (strncmp(text, "RTEXT|", 6) == 0) { ui_rtext(text + 6); return UI_READER_NONE; }
    if (strncmp(text, "RFOOT|", 6) == 0) return ui_rfoot(text + 6);
    return UI_READER_NONE;
}

#endif  // KYPHONE_UI_READER_H
