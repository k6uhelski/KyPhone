#include <Inkplate.h>
#include <driver/gpio.h>
#include "soc/io_mux_reg.h"
#include <ctype.h>

Inkplate display(INKPLATE_1BIT); 

// OS 0.2.1 screen renderers (also compiled on a computer by tests/firmware_host).
#include "ui_screens.h"
#include "ui_reader.h"
#include "version.h"      // KYPHONE_VERSION, generated from spi_bridge/version.py

// Shared pins
#define PIN_MOSI 13
#define PIN_SCLK 14
#define PIN_CS   15

// Handshake
#define PIN_HANDSHAKE IO_PIN_B0 // P1-0 expander pin
#define FRAME_SILENCE_US 30000UL  // clock silence that ends a frame (600 ms until 0.3.1, 150 ms until 0.6.3; see loop())
#define FRAME_CHECKED 0xA5         // first byte of a checked frame: [0xA5, crc8(bytes 3..255), 0x02, text...]

// --- ISR Variables ---
#define PAYLOAD_BYTES 256
#define TOTAL_BITS (PAYLOAD_BYTES * 8)

volatile uint8_t rx_buf[PAYLOAD_BYTES];
unsigned long last_full_refresh_ms = 0;  // time-based full refresh cadence (ghost clear)
#define FULL_REFRESH_INTERVAL_MS 600000UL  // full refresh every 10 minutes; partialUpdate() otherwise
bool did_boot_full_refresh = false;  // force one full refresh on the first screen after boot —
                                      // the panel may still show a stale image from before this
                                      // flash, and partialUpdate() alone won't clear it
char current_screen[32] = "BOOT";
volatile uint16_t bit_counter = 0;
volatile bool transfer_complete = false;
volatile uint32_t last_sclk_time = 0;
volatile uint32_t last_cs_time = 0;

// Debug Counters
volatile uint32_t debug_cs_falling = 0;
volatile uint32_t debug_sclk_total = 0;
volatile uint32_t first_sclk_time = 0;

// --- RECLAIM SPI PINS FROM INKPLATE LIBRARY ---
void reclaim_spi_pins_for_gpio() {
    // GPIO 15 (CS): Force out of SPI CS0 function, add pull-up vs strapping pull-down
    PIN_FUNC_SELECT(IO_MUX_GPIO15_REG, 2);
    gpio_set_pull_mode((gpio_num_t)PIN_CS, GPIO_PULLUP_ONLY);
    gpio_config_t cs_conf = {
        .pin_bit_mask = (1ULL << PIN_CS),
        .mode         = GPIO_MODE_INPUT,
        .pull_up_en   = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    gpio_config(&cs_conf);

    // GPIO 13 (MOSI): Force out of SPI MOSI function — idles LOW in SPI mode, masking data
    PIN_FUNC_SELECT(IO_MUX_GPIO13_REG, 2);
    gpio_config_t mosi_conf = {
        .pin_bit_mask = (1ULL << PIN_MOSI),
        .mode         = GPIO_MODE_INPUT,
        .pull_up_en   = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_ENABLE,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    gpio_config(&mosi_conf);
}

// --- ISR: SPI Clock ---
void IRAM_ATTR spi_clock_isr() {
    uint32_t now = micros();
    debug_sclk_total++;
    if (bit_counter == 0) first_sclk_time = now;
    last_sclk_time = now;

    if (bit_counter == 0) {
        // First edge: signal busy immediately so Master won't re-send
        // Note: digitalWriteIO is not ISR-safe; handled in loop() via flag
        // This comment left intentionally — handshake pulled LOW in loop() on first bit
    }

    if (bit_counter < TOTAL_BITS) {
        bool mosi_high = (GPIO.in >> PIN_MOSI) & 0x1; // Faster read
        uint8_t byte_idx = bit_counter / 8;
        uint8_t bit_idx = 7 - (bit_counter % 8); 
        
        if (mosi_high) rx_buf[byte_idx] |= (1 << bit_idx);
        else           rx_buf[byte_idx] &= ~(1 << bit_idx);
        
        bit_counter++;
    }
}

// --- ISR: Chip Select (Falling) ---
void IRAM_ATTR cs_falling_isr() {
    uint32_t now = micros();
    // Debounce: Ignore edges within 1ms of each other
    if (now - last_cs_time > 1000) {
        debug_cs_falling++;
        bit_counter = 0;
        transfer_complete = false;
        // Don't zero the whole buffer in ISR if we are noisy
    }
    last_cs_time = now;
}

// --- Screen Renderers ---

void render_home(char* data) {
    // data = "HH:MM|Day, Mon DD|{unread}|{home_sel}"
    // home_sel: -1=none, 0=TEXT, 1=CALL, 2=READ, 3=LISTEN
    char time_str[16] = "";
    char date_str[32] = "";
    int unread = 0;
    int home_sel = -1;

    char* p1 = strchr(data, '|');
    if (p1 != NULL) {
        snprintf(time_str, sizeof(time_str), "%.*s", (int)(p1 - data), data);
        char* p2 = strchr(p1 + 1, '|');
        if (p2 != NULL) {
            snprintf(date_str, sizeof(date_str), "%.*s", (int)(p2 - p1 - 1), p1 + 1);
            char* p3 = strchr(p2 + 1, '|');
            if (p3 != NULL) {
                char unread_buf[8] = "";
                snprintf(unread_buf, sizeof(unread_buf), "%.*s", (int)(p3 - p2 - 1), p2 + 1);
                unread = atoi(unread_buf);
                home_sel = atoi(p3 + 1);
            } else {
                unread = atoi(p2 + 1);
            }
        } else {
            strncpy(date_str, p1 + 1, sizeof(date_str) - 1);
        }
    } else {
        strncpy(time_str, data, sizeof(time_str) - 1);
    }

    // ASCII art — top right, textSize 2 = 12px wide x 16px tall per char
    const char* cat[] = {
        "   )\\._.,--....,'``.",
        "  /,   _.. \\   _\\  (`._ ,.",
        " `._.-(,_..'--(,_..'`-.;.'",
    };
    display.setTextSize(2);
    for (int ci = 0; ci < 3; ci++) {
        int lw = strlen(cat[ci]) * 12;
        int cx = 600 - lw - 6, cy = 6 + ci * 18;
        display.setCursor(cx, cy);     display.print(cat[ci]);
        display.setCursor(cx + 1, cy); display.print(cat[ci]);
    }

    // Clock + date vertically centered above button row (y=35 to ~520)
    int total_h = 80 + 24 + 24;
    int start_y = 35 + (475 - total_h) / 2;

    // Clock — textSize 8 = 48px wide per char, 64px tall
    display.setTextSize(8);
    int clock_w = strlen(time_str) * 48;
    display.setCursor((600 - clock_w) / 2, start_y);
    display.print(time_str);

    // Date — textSize 3 = 18px wide per char, 24px tall
    display.setTextSize(3);
    int date_w = strlen(date_str) * 18;
    display.setCursor((600 - date_w) / 2, start_y + 80 + 24);
    display.print(date_str);

    // 4 buttons: Texts, Calls, Books, Music
    const char* buttons[] = {"Texts", "Calls", "Books", "Music"};
    int btn_positions[] = {0, 150, 300, 450};
    int btn_w = 150, btn_h = 65, btn_y = 535;

    for (int i = 0; i < 4; i++) {
        int bx = btn_positions[i];
        bool selected = (i == home_sel);
        int cw = strlen(buttons[i]) * 12; // textSize 2: 6*2=12px per char
        int lx = bx + (btn_w - cw) / 2;
        int ly = btn_y + (btn_h - 16) / 2; // 16 = textSize 2 height

        if (selected) {
            display.fillRect(bx, btn_y, btn_w, btn_h, BLACK);
            display.setTextColor(WHITE);
        } else {
            display.drawRect(bx, btn_y, btn_w, btn_h, BLACK);
            display.setTextColor(BLACK);
        }
        display.setTextSize(2);
        display.setCursor(lx, ly);     display.print(buttons[i]);
        display.setCursor(lx + 1, ly); display.print(buttons[i]);
        display.setTextColor(BLACK);

        // Unread badge on TEXT button (index 0)
        if (i == 0 && unread > 0) {
            int badge_size = 24;
            int bx2 = bx + 2, by2 = btn_y + 2;
            if (selected) {
                display.fillRect(bx2, by2, badge_size, badge_size, WHITE);
                display.setTextColor(BLACK);
            } else {
                display.fillRect(bx2, by2, badge_size, badge_size, BLACK);
                display.setTextColor(WHITE);
            }
            char badge_label[2] = {'0' + (char)(unread > 9 ? 9 : unread), '\0'};
            int tx = bx2 + (badge_size - 12) / 2;
            int ty = by2 + (badge_size - 16) / 2;
            display.setTextSize(2);
            display.setCursor(tx, ty);
            display.print(badge_label);
            display.setTextColor(BLACK);
        }
    }
}

void render_msg_list(char* data, int selected) {
    // data = "Name·preview·time|Name·preview·time|..."
    const int header_h = 44;
    const int row_h    = 72;
    const int margin   = 16;

    // Header bar — outline style: < TEXT +
    display.drawLine(0, header_h - 1, 600, header_h - 1, BLACK);
    display.setTextSize(3);

    // < back — invert when selected == -1
    if (selected == -1) {
        int cw = 18, ch = 24;
        display.fillRect(margin - 4, 6, cw + 8, ch + 8, BLACK);
        display.setTextColor(WHITE);
        display.setCursor(margin, 10);
        display.print("<");
        display.setTextColor(BLACK);
    } else {
        display.setTextColor(BLACK);
        display.setCursor(margin, 10);
        display.print("<");
    }

    // TEXTS centered (5 chars * 18px = 90px wide)
    display.setTextColor(BLACK);
    display.setCursor((600 - 90) / 2, 10);
    display.print("TEXTS");

    // + right — invert when selected == -2
    int plus_x = 600 - margin - 18;
    if (selected == -2) {
        int cw = 18, ch = 24;
        display.fillRect(plus_x - 4, 6, cw + 8, ch + 8, BLACK);
        display.setTextColor(WHITE);
        display.setCursor(plus_x, 10);
        display.print("+");
        display.setTextColor(BLACK);
    } else {
        display.setTextColor(BLACK);
        display.setCursor(plus_x, 10);
        display.print("+");
    }

    int y = header_h;
    int row = 0;
    char* entry = data;

    while (entry != NULL && y + row_h <= 600) {
        char* next = strchr(entry, '|');
        char entry_buf[80] = "";
        if (next != NULL) {
            strncpy(entry_buf, entry, next - entry);
            entry = next + 1;
        } else {
            strncpy(entry_buf, entry, sizeof(entry_buf) - 1);
            entry = NULL;
        }

        // Parse name·preview·time
        char name_buf[16]    = "";
        char preview_buf[50] = "";
        char time_buf[12]    = "";

        char* dot1 = strchr(entry_buf, '\xB7');
        if (dot1 != NULL) {
            snprintf(name_buf, sizeof(name_buf), "%.*s", (int)(dot1 - entry_buf), entry_buf);
            char* dot2 = strchr(dot1 + 1, '\xB7');
            if (dot2 != NULL) {
                snprintf(preview_buf, sizeof(preview_buf), "%.*s", (int)(dot2 - dot1 - 1), dot1 + 1);
                strncpy(time_buf, dot2 + 1, sizeof(time_buf) - 1);
            } else {
                strncpy(preview_buf, dot1 + 1, sizeof(preview_buf) - 1);
            }
        } else {
            strncpy(name_buf, entry_buf, sizeof(name_buf) - 1);
        }

        bool is_sel = (row == selected);
        if (is_sel) {
            display.fillRect(0, y, 600, row_h, BLACK);
            display.setTextColor(WHITE);
        }

        // Name — textSize 3, left, bold
        display.setTextSize(3);
        display.setCursor(margin, y + 8);     display.print(name_buf);
        display.setCursor(margin + 1, y + 8); display.print(name_buf);

        // Chevron — textSize 2, right, vertically centered
        int chevron_x = 600 - margin - 12; // 12 = 1 char at textSize 2
        display.setTextSize(2);
        display.setCursor(chevron_x, y + (row_h - 16) / 2);
        display.print(">");

        // Timestamp — textSize 2, left of chevron
        if (strlen(time_buf) > 0) {
            int ts_w = strlen(time_buf) * 12; // textSize 2: 12px per char
            display.setCursor(chevron_x - ts_w - 8, y + 16);
            display.print(time_buf);
        }

        // Preview — textSize 2, left
        display.setCursor(margin, y + 40);
        display.print(preview_buf);

        if (is_sel) {
            display.setTextColor(BLACK);
        }

        display.drawLine(0, y + row_h - 1, 600, y + row_h - 1, BLACK);
        row++;
        y += row_h;
    }
}

// Render word-wrapped text, advancing *y by line_h per line.
// right_align=true: each line is right-aligned to right margin.
void render_wrapped(const char* text, bool right_align, int* y, int line_h) {
    const int margin = 20;
    const int char_w = 18; // textSize 3: 6*3
    const int max_chars = (600 - margin * 2) / char_w; // ~30 chars

    char line_buf[64] = "";
    const char* p = text;

    while (true) {
        // Find next word
        const char* word_start = p;
        while (*p && *p != ' ') p++;
        int word_len = p - word_start;

        if (word_len > 0) {
            int line_len = strlen(line_buf);
            bool fits = (line_len == 0) ? (word_len <= max_chars)
                                        : (line_len + 1 + word_len <= max_chars);
            if (fits) {
                if (line_len > 0) strcat(line_buf, " ");
                strncat(line_buf, word_start, word_len);
            } else {
                // Flush current line
                if (line_len > 0) {
                    int x = right_align ? (600 - margin - (int)strlen(line_buf) * char_w) : margin;
                    if (x < margin) x = margin;
                    display.setCursor(x, *y);
                    display.print(line_buf);
                    *y += line_h;
                    memset(line_buf, 0, sizeof(line_buf));
                }
                strncat(line_buf, word_start, word_len < 62 ? word_len : 62);
            }
        }

        if (*p == '\0') break;
        p++; // skip space
    }

    // Flush remaining
    if (strlen(line_buf) > 0) {
        int x = right_align ? (600 - margin - (int)strlen(line_buf) * char_w) : margin;
        if (x < margin) x = margin;
        display.setCursor(x, *y);
        display.print(line_buf);
        *y += line_h;
    }
}

void render_msg_thread(char* data) {
    // data = "Name|Y:time~body|R:time~body|..."
    char name_buf[32] = "";
    char* pipe = strchr(data, '|');
    if (pipe != NULL) {
        snprintf(name_buf, sizeof(name_buf), "%.*s", (int)(pipe - data), data);
        data = pipe + 1;
    } else {
        strncpy(name_buf, data, sizeof(name_buf) - 1);
        data = NULL;
    }

    // Centered name header
    display.setTextSize(3);
    int name_x = (600 - (int)strlen(name_buf) * 18) / 2;
    if (name_x < 10) name_x = 10;
    display.setCursor(name_x, 10);     display.print(name_buf);
    display.setCursor(name_x + 1, 10); display.print(name_buf);
    display.drawLine(0, 46, 600, 46, BLACK);

    int y = 56;
    const int line_h = 32;   // textSize 3: 24px + 8px gap
    const int margin = 16;
    char last_time[12] = "";

    while (data != NULL && y < 570) {
        char* next = strchr(data, '|');
        char msg_buf[80] = "";
        if (next != NULL) {
            strncpy(msg_buf, data, next - data);
            data = next + 1;
        } else {
            strncpy(msg_buf, data, sizeof(msg_buf) - 1);
            data = NULL;
        }

        char align = 'R';
        char* rest = msg_buf;
        if (strlen(msg_buf) >= 2 && msg_buf[1] == ':') {
            align = msg_buf[0];
            rest = msg_buf + 2;
        }

        // Split time~body
        char time_buf[12] = "";
        char* body = rest;
        char* tilde = strchr(rest, '~');
        if (tilde != NULL) {
            strncpy(time_buf, rest, tilde - rest);
            body = tilde + 1;
        }

        // Time separator if new time
        if (strlen(time_buf) > 0 && strcmp(time_buf, last_time) != 0) {
            strncpy(last_time, time_buf, sizeof(last_time) - 1);
            display.setTextSize(1);
            int tw = strlen(time_buf) * 6;
            display.setCursor((600 - tw) / 2, y);
            display.print(time_buf);
            y += 16;
        }

        // AIM style: prefix on first line, body word-wrapped with continuation indent
        const char* label = (align == 'Y') ? "Me" : name_buf;
        char prefix_str[24] = "";
        snprintf(prefix_str, sizeof(prefix_str), "%s: ", label);
        int prefix_px = (int)strlen(prefix_str) * 18; // textSize 3: 18px/char
        int body_x = margin + prefix_px;
        int chars_per_line = (600 - body_x) / 18;
        if (chars_per_line < 1) chars_per_line = 1;

        display.setTextSize(3);
        display.setCursor(margin, y);     display.print(prefix_str);
        display.setCursor(margin + 1, y); display.print(prefix_str);

        char wbuf[48] = "";
        const char* p = body;
        while (*p != '\0') {
            while (*p == ' ') p++;
            if (*p == '\0') break;
            const char* ws = p;
            while (*p && *p != ' ') p++;
            int wlen = (int)(p - ws);
            int blen = (int)strlen(wbuf);
            bool fits = (blen == 0) ? (wlen <= chars_per_line)
                                    : (blen + 1 + wlen <= chars_per_line);
            if (fits) {
                if (blen > 0 && blen < (int)sizeof(wbuf) - 2) strcat(wbuf, " ");
                int avail = (int)sizeof(wbuf) - (int)strlen(wbuf) - 1;
                strncat(wbuf, ws, avail < wlen ? avail : wlen);
            } else {
                display.setCursor(body_x, y);
                display.print(wbuf);
                y += line_h;
                memset(wbuf, 0, sizeof(wbuf));
                strncat(wbuf, ws, wlen < (int)sizeof(wbuf) - 1 ? wlen : (int)sizeof(wbuf) - 1);
            }
        }
        display.setCursor(body_x, y);
        display.print(wbuf);
        y += line_h;
        y += 4;
    }
}

void render_sms(char* text) {
    // "SENDER|body" — two-line SMS format
    char* pipe = strchr(text, '|');
    if (pipe != NULL) {
        *pipe = '\0';
        display.setTextSize(3);
        display.setCursor(10, 10);
        display.print(text);
        display.setTextSize(4);
        display.setCursor(10, 60);
        display.print(pipe + 1);
    } else {
        display.setTextSize(4);
        display.setCursor(10, 10);
        display.print(text);
    }
}

// ══════════════════════════════════════════════════════════════
// OS 0.1 RENDERERS
// ══════════════════════════════════════════════════════════════

// Word-wrap text, centering each line. Returns final y after all lines.
int render_centered_wrapped(const char* text, int y, int line_h, int text_size) {
    const int char_w    = 6 * text_size;
    const int margin    = 60;
    const int max_chars = (600 - margin * 2) / char_w;
    char line_buf[80]   = "";
    const char* p = text;
    while (true) {
        while (*p == ' ') p++;
        if (*p == '\0') break;
        const char* ws = p;
        while (*p && *p != ' ') p++;
        int wlen = (int)(p - ws);
        if (wlen == 0) break;
        int blen = (int)strlen(line_buf);
        bool fits = (blen == 0) ? (wlen <= max_chars) : (blen + 1 + wlen <= max_chars);
        if (fits) {
            if (blen > 0 && blen < 78) strcat(line_buf, " ");
            int avail = 78 - (int)strlen(line_buf);
            strncat(line_buf, ws, avail < wlen ? avail : wlen);
        } else {
            if (blen > 0) {
                int x = (600 - (int)strlen(line_buf) * char_w) / 2;
                if (x < margin) x = margin;
                display.setCursor(x, y);
                display.print(line_buf);
                y += line_h;
                memset(line_buf, 0, sizeof(line_buf));
            }
            int avail = 78;
            strncat(line_buf, ws, avail < wlen ? avail : wlen);
        }
    }
    if (strlen(line_buf) > 0) {
        int x = (600 - (int)strlen(line_buf) * char_w) / 2;
        if (x < margin) x = margin;
        display.setCursor(x, y);
        display.print(line_buf);
        y += line_h;
    }
    return y;
}

// ASCII cat, pre-rendered to a fixed 1-bit bitmap (Courier Bold) so it always
// looks identical regardless of the display's live text-rendering behavior.
// Generated from the same art previously drawn with display.print().
#define CAT_BITMAP_W 322
#define CAT_BITMAP_H 80

const uint8_t cat_bitmap[] PROGMEM = {
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x03, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x30, 0x03, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x07, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xF0, 0x3C, 0x03, 0xC0, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x06, 0x07, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0xF0, 0x3E, 0x03, 0xE0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x0F, 0x03, 0x80,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0xF0, 0x3F, 0x83, 0xF8, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x07, 0x03, 0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xF0, 0x0F,
    0x80, 0xF8, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x07, 0x81, 0xC0, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0xF0, 0x03, 0x80, 0x38, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x07, 0x81, 0xE0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x60, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x03, 0x80, 0xE0, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x03, 0x80,
    0xF0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x03, 0x80, 0xF0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x7F, 0xE7, 0xFE, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x03, 0x80, 0x78, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x7F, 0xE7, 0xFE, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x03, 0x80, 0x78, 0x07, 0x00, 0x00, 0x07, 0x00, 0xF8, 0x7F, 0xE7,
    0xFE, 0x07, 0x00, 0x70, 0x07, 0x00, 0x70, 0x0F, 0x80, 0x00, 0x00, 0x00,
    0x00, 0x07, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x03, 0x80, 0x3C, 0x0F, 0x80, 0x00,
    0x0F, 0x80, 0xF8, 0x00, 0x00, 0x00, 0x0F, 0x80, 0xF8, 0x0F, 0x80, 0xF8,
    0x0F, 0x80, 0x00, 0x00, 0x00, 0x00, 0x0F, 0x80, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x03,
    0x80, 0x3C, 0x1F, 0x80, 0x00, 0x1F, 0x80, 0xF0, 0x00, 0x00, 0x00, 0x1F,
    0x81, 0xF8, 0x1F, 0x81, 0xF8, 0x0F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x1F,
    0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x03, 0x80, 0x1E, 0x0F, 0x80, 0x00, 0x0F, 0x81,
    0xF0, 0x00, 0x00, 0x00, 0x0F, 0x80, 0xF8, 0x0F, 0x80, 0xF8, 0x1F, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x0F, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x07, 0x80, 0x1E,
    0x0F, 0x00, 0x00, 0x0F, 0x01, 0xE0, 0x00, 0x00, 0x00, 0x0F, 0x00, 0xF0,
    0x0F, 0x00, 0xF0, 0x1E, 0x00, 0x00, 0x00, 0x00, 0x00, 0x0F, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x07, 0x80, 0x0C, 0x00, 0x00, 0x00, 0x00, 0x01, 0xE0, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x1E, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x07, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x01, 0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x1C, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x0F, 0x00, 0x00, 0x00, 0x1F, 0xFF, 0x80, 0x01, 0xC0, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x1C, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x06, 0x00, 0x00, 0x00, 0x1F, 0xFF, 0x80,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x1F, 0xFF, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x30, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x03, 0x00, 0x00, 0x00, 0x00, 0x00, 0x03, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x0E, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x70,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x07, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x03, 0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x1E, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x78, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x07,
    0x80, 0x00, 0x00, 0x00, 0x06, 0x03, 0xE0, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x1C, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x38, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x03, 0x80, 0x00, 0x00, 0x00, 0x0F, 0x03, 0xF8,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x3C, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x3C, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x03, 0xC0, 0x00,
    0x00, 0x00, 0x0E, 0x00, 0xF8, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x38, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x1C, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x01, 0xC0, 0x00, 0x00, 0x00, 0x1E, 0x00, 0x38, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x78,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x1E, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0xE0, 0x00, 0x00, 0x00,
    0x1E, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x70, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x0E, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0xE0, 0x00, 0x00, 0x00, 0x1C, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xF0, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x0F, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xF0, 0x00, 0x00, 0x00, 0x1C, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0xF0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x0F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xF0,
    0x00, 0x00, 0x00, 0x1C, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0xE0, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x07, 0x80, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x78, 0x00, 0x00, 0x00, 0x1C, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01,
    0xE0, 0x0F, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x70, 0x07, 0x00,
    0x00, 0x07, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x78, 0x00, 0x00,
    0x00, 0x1C, 0x00, 0x00, 0x07, 0x00, 0x00, 0x00, 0x00, 0xF8, 0x07, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x03, 0xC0, 0x0F, 0x80, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0xF8, 0x0F, 0x80, 0x00, 0x03, 0xC0, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x3C, 0x00, 0x00, 0x00, 0x1C, 0x00, 0x00, 0x0F, 0x80, 0x00,
    0x00, 0x00, 0xF8, 0x0F, 0x80, 0x00, 0x00, 0x00, 0x00, 0x03, 0xC0, 0x0F,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0xF8, 0x1F, 0x80, 0x00, 0x03,
    0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x3C, 0x00, 0x00, 0x00, 0x1C,
    0x00, 0x00, 0x1F, 0x80, 0x00, 0x00, 0x00, 0xF0, 0x1F, 0x80, 0x00, 0x00,
    0x00, 0x00, 0x07, 0x80, 0x1F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0xF8, 0x0F, 0x80, 0x00, 0x01, 0xE0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x1E, 0x00, 0x00, 0x00, 0x1C, 0x00, 0x00, 0x0F, 0x80, 0x00, 0x00, 0x01,
    0xF0, 0x0F, 0x80, 0x00, 0x00, 0x00, 0x00, 0x07, 0x80, 0x1E, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0xF0, 0x0F, 0x00, 0x00, 0x01, 0xE0, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x1E, 0x00, 0x00, 0x00, 0x1E, 0x00, 0x00,
    0x0F, 0x00, 0x00, 0x00, 0x01, 0xE0, 0x0F, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x03, 0x00, 0x1E, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x00,
    0x00, 0x00, 0x1E, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0xE0, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x1C, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x0E, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x01, 0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x1C, 0x00, 0x00, 0x00, 0x00, 0x01, 0xFF, 0xF8, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0xFF, 0xF8, 0x00, 0x00, 0x00, 0x00,
    0x0F, 0x00, 0x00, 0x00, 0x1F, 0xFF, 0x80, 0x01, 0xC0, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0xFF,
    0xF8, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0xFF,
    0xF8, 0x00, 0x00, 0x00, 0x00, 0x06, 0x00, 0x00, 0x00, 0x1F, 0xFF, 0x80,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x01, 0xFF, 0xF8, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x01, 0xFF, 0xF8, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x1F, 0xFF, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x30, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x03, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x3C, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x0F,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x0F,
    0x03, 0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x0F, 0x00, 0x00, 0x00,
    0x00, 0x3E, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x60, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x0F, 0x00, 0x00, 0x00, 0x00, 0x60, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x0F, 0x03, 0xE0, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x0F, 0x00, 0x00, 0x00, 0x00, 0x3F, 0x80, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0xF0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x0F, 0x00, 0x00,
    0x00, 0x00, 0xF0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x0F, 0x03, 0xF8,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x0F, 0x00, 0x00, 0x00, 0x00, 0x0F,
    0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xE0, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x0F, 0x00, 0x00, 0x00, 0x00, 0xE0, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x0F, 0x00, 0xF8, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x0F,
    0x00, 0x00, 0x00, 0x00, 0x03, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01,
    0xE0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x0F, 0x00, 0x00, 0x00, 0x01,
    0xE0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x0F, 0x00, 0x38, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x0F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x01, 0xE0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x06, 0x00, 0x00, 0x00, 0x01, 0xE0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x06, 0x00, 0x00, 0x00, 0x00, 0x00, 0x0F, 0x00, 0x00, 0x06, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0xC0, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0xC0, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x0F,
    0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x01, 0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x01, 0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x1F, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x7F, 0xE1, 0xC0, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x07, 0xFE, 0x7F, 0xE1, 0xC0, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x7F, 0xE0, 0x00, 0x0F, 0x80, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x7F,
    0xE1, 0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x07, 0xFE, 0x7F,
    0xE1, 0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x7F,
    0xE0, 0x00, 0x0F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x70, 0x00, 0x00, 0x70, 0x7F, 0xE1, 0xC0, 0x0F, 0x80, 0x00, 0x07, 0x00,
    0x70, 0x00, 0x07, 0xFE, 0x7F, 0xE1, 0xC0, 0x0F, 0x80, 0x00, 0x07, 0x00,
    0x70, 0x00, 0x00, 0x00, 0x7F, 0xE0, 0x70, 0x0F, 0x00, 0x70, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0xF8, 0x00, 0x00, 0xF8, 0x00, 0x01, 0xC0,
    0x0F, 0x80, 0x00, 0x0F, 0x80, 0xF8, 0x00, 0x00, 0x00, 0x00, 0x01, 0xC0,
    0x0F, 0x80, 0x00, 0x0F, 0x80, 0xF8, 0x00, 0x00, 0x00, 0x00, 0x00, 0xF8,
    0x0F, 0x80, 0xF8, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0xF8, 0x00,
    0x01, 0xF8, 0x00, 0x01, 0xC0, 0x0F, 0x00, 0x00, 0x1F, 0x81, 0xF8, 0x00,
    0x00, 0x00, 0x00, 0x01, 0xC0, 0x0F, 0x00, 0x00, 0x1F, 0x81, 0xF8, 0x00,
    0x00, 0x00, 0x00, 0x01, 0xF8, 0x1F, 0x01, 0xF8, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0xF8, 0x00, 0x00, 0xF8, 0x00, 0x01, 0xC0, 0x1F, 0x00,
    0x00, 0x0F, 0x80, 0xF8, 0x00, 0x00, 0x00, 0x00, 0x01, 0xC0, 0x1F, 0x00,
    0x00, 0x0F, 0x80, 0xF8, 0x00, 0x00, 0x00, 0x00, 0x00, 0xF8, 0x1F, 0x00,
    0xF8, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xF0, 0x00, 0x00, 0xF0,
    0x00, 0x01, 0xE0, 0x1E, 0x00, 0x00, 0x0F, 0x00, 0xF0, 0x00, 0x00, 0x00,
    0x00, 0x01, 0xE0, 0x1E, 0x00, 0x00, 0x0F, 0x00, 0xF0, 0x00, 0x00, 0x00,
    0x00, 0x00, 0xF0, 0x1E, 0x00, 0xF0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0xE0, 0x1E, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0xE0, 0x1E, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x1E, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0xE0, 0x1C, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0xE0, 0x1C, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x1C, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01,
    0xFF, 0xF8, 0x00, 0x00, 0x00, 0xF0, 0x1C, 0x1F, 0xFF, 0x80, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0xF0, 0x1C, 0x1F, 0xFF, 0x80, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x1C, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x01, 0xFF, 0xF8, 0x00, 0x00, 0x00, 0x60, 0x00,
    0x1F, 0xFF, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x60, 0x00,
    0x1F, 0xFF, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0xFF, 0xF8,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x1F, 0xFF, 0x80, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x1F, 0xFF, 0x80, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00
};

// The front light (Inkplate 4 TEMPERA). The Radxa sends the level (0 off .. 8) in LOCK and LIGHTSET commands; the
// light is hardware state, so it is set here, not drawn.
void apply_light(int level) {
    if (level <= 0) {
        display.frontlight.setState(false);
        return;
    }
    if (level > 8) level = 8;
    display.frontlight.setState(true);
    display.frontlight.setBrightness((uint8_t)(level * 63 / 8));
}

void render_lock(char* data) {
    // data = "time_str|date_str|quote|attribution|version|mark|light"
    char time_str[16] = "", date_str[32] = "", quote_buf[200] = "", attr_buf[40] = "", version_buf[16] = "";
    char* p1 = strchr(data, '|');
    if (p1) {
        snprintf(time_str, sizeof(time_str), "%.*s", (int)(p1 - data), data);
        char* p2 = strchr(p1 + 1, '|');
        if (p2) {
            snprintf(date_str, sizeof(date_str), "%.*s", (int)(p2 - p1 - 1), p1 + 1);
            char* p3 = strchr(p2 + 1, '|');
            if (p3) {
                snprintf(quote_buf, sizeof(quote_buf), "%.*s", (int)(p3 - p2 - 1), p2 + 1);
                strncpy(attr_buf, p3 + 1, sizeof(attr_buf) - 1);
            } else {
                strncpy(quote_buf, p2 + 1, sizeof(quote_buf) - 1);
            }
        } else {
            strncpy(date_str, p1 + 1, sizeof(date_str) - 1);
        }
    } else {
        strncpy(time_str, data, sizeof(time_str) - 1);
    }

    // The attribution field carries the Radxa's version after its own '|' (older Radxa software sends none).
    char* pv = strchr(attr_buf, '|');
    char mark = '\0';
    if (pv) {
        snprintf(version_buf, sizeof(version_buf), "%s", pv + 1);
        *pv = '\0';
        // After the version: the new-activity mark ("*" or empty), then the screen light level (newer Radxa software).
        char* pm = strchr(version_buf, '|');
        if (pm) {
            *pm = '\0';
            char* pl = strchr(pm + 1, '|');
            mark = pm[1] == '*' ? '*' : '\0';
            if (pl && pl[1] >= '0' && pl[1] <= '9') apply_light(atoi(pl + 1));
        }
    }
    if (version_buf[0] && strcmp(version_buf, KYPHONE_VERSION) != 0) {
        Serial.printf(">> WARNING: the Radxa runs OS %s but this firmware is %s - flash and deploy them together\n",
                      version_buf, KYPHONE_VERSION);
    }

    display.setTextColor(BLACK);

    // "OS <version>" — bottom left, textSize 2 (18px design token): the version the Radxa reports
    if (version_buf[0]) {
        char label[24];
        snprintf(label, sizeof(label), "OS %s", version_buf);
        display.setTextSize(2);
        display.setCursor(10, 600 - 8 - 16);
        display.print(label);
    }

    // ASCII cat — bottom right, drawn as a fixed bitmap (see cat_bitmap above); unchanged since 0.1
    display.drawBitmap(600 - CAT_BITMAP_W - 6, 600 - CAT_BITMAP_H - 6, cat_bitmap, CAT_BITMAP_W, CAT_BITMAP_H, BLACK);

    // Clock — textSize 8, centered, top:159
    display.setTextSize(8);
    int clock_w = strlen(time_str) * 48;
    display.setCursor((600 - clock_w) / 2, 159);
    display.print(time_str);
    if (mark == '*') {                                          // new activity: a small * just right of the clock
        display.setTextSize(4);
        display.setCursor((600 + clock_w) / 2 + 8, 159);
        display.print("*");
    }

    // Date — textSize 3, centered, top:243
    display.setTextSize(3);
    int date_w = strlen(date_str) * 18;
    display.setCursor((600 - date_w) / 2, 243);
    display.print(date_str);

    // Quote — textSize 3, centered, wrapped, top:356, line-height 34
    display.setTextSize(3);
    int quote_end_y = render_centered_wrapped(quote_buf, 356, 34, 3);

    // Attribution — textSize 2, centered, 12px below the quote block
    if (strlen(attr_buf) > 0) {
        display.setTextSize(2);
        int aw = strlen(attr_buf) * 12;
        display.setCursor((600 - aw) / 2, quote_end_y + 12);
        display.print(attr_buf);
    }
}


// Battery block + percentage + 4-bar signal staircase, right-aligned in the
// home header. No real telemetry exists yet — fixed placeholder values,
// swappable for real readings later.
void render_status_group(uint16_t fg, uint16_t bg, int mid_y) {
    const int batt_pct = 82;
    char pct_str[8];
    snprintf(pct_str, sizeof(pct_str), "%d%%", batt_pct);
    int pct_w = strlen(pct_str) * 18; // textSize 3

    const int sig_heights[] = {5, 9, 13, 17};
    const int sig_w = 4, sig_gap = 3;
    int sig_group_w = sig_w * 4 + sig_gap * 3;

    const int batt_w = 34, batt_h = 18, batt_border = 2;
    const int nub_w = 3, nub_h = 8;

    int total_w = batt_w + 2 + nub_w + 14 + pct_w + 14 + sig_group_w;
    int x = 600 - 24 - total_w;

    int by = mid_y - batt_h / 2;
    display.drawRect(x, by, batt_w, batt_h, fg);
    display.drawRect(x + 1, by + 1, batt_w - 2, batt_h - 2, fg);
    int fill_w = (int)((batt_w - 2 * batt_border) * (batt_pct / 100.0));
    display.fillRect(x + batt_border, by + batt_border, fill_w, batt_h - 2 * batt_border, fg);
    x += batt_w + 2;
    display.fillRect(x, mid_y - nub_h / 2, nub_w, nub_h, fg);
    x += nub_w + 14;

    display.setTextSize(3);
    display.setTextColor(fg);
    display.setCursor(x, mid_y - 12);
    display.print(pct_str);
    x += pct_w + 14;

    int sig_bottom = mid_y + sig_heights[3] / 2;
    for (int i = 0; i < 4; i++) {
        int h = sig_heights[i];
        if (i == 3) {
            display.drawRect(x, sig_bottom - h, sig_w, h, fg);
        } else {
            display.fillRect(x, sig_bottom - h, sig_w, h, fg);
        }
        x += sig_w + sig_gap;
    }
}

// Shared back/title/+ header used by list screens — each control is a
// literal 38x34 hit box that inverts when selected.
void render_header_bar(const char* title, bool back_active, bool plus_active, int height) {
    display.drawLine(0, height - 1, 600, height - 1, BLACK);
    display.setTextColor(BLACK);
    const int box_w = 38, box_h = 34;

    if (back_active) {
        display.fillRect(16, 6, box_w, box_h, BLACK);
        display.setTextColor(WHITE);
    }
    display.setTextSize(3);
    display.setCursor(16 + (box_w - 18) / 2, 6 + (box_h - 24) / 2);
    display.print("<");
    display.setTextColor(BLACK);

    int title_w = strlen(title) * 18;
    display.setCursor((600 - title_w) / 2, 10);     display.print(title);
    display.setCursor((600 - title_w) / 2 + 1, 10); display.print(title);

    int plus_x = 600 - 16 - box_w;
    if (plus_active) {
        display.fillRect(plus_x, 6, box_w, box_h, BLACK);
        display.setTextColor(WHITE);
    }
    display.setCursor(plus_x + (box_w - 18) / 2, 6 + (box_h - 24) / 2);
    display.print("+");
    display.setTextColor(BLACK);
}


// Word-wrap text into fixed-size line buffers (max_chars per line), up to
// max_lines. Returns the number of lines produced (always >= 1).
int wrap_into_lines(const char* text, int max_chars, char lines_out[][32], int max_lines) {
    if (max_chars > 31) max_chars = 31;
    int n = 0;
    char cur[32] = "";
    const char* p = text;
    while (*p != '\0' && n < max_lines) {
        while (*p == ' ') p++;
        if (*p == '\0') break;
        const char* ws = p;
        while (*p && *p != ' ') p++;
        int wlen = (int)(p - ws);
        int blen = (int)strlen(cur);
        bool fits = (blen == 0) ? (wlen <= max_chars) : (blen + 1 + wlen <= max_chars);
        if (fits) {
            if (blen > 0 && blen < 30) strcat(cur, " ");
            int avail = 31 - (int)strlen(cur);
            strncat(cur, ws, avail < wlen ? avail : wlen);
        } else {
            strncpy(lines_out[n], cur, 31); lines_out[n][31] = '\0'; n++;
            memset(cur, 0, sizeof(cur));
            if (n >= max_lines) break;
            strncat(cur, ws, wlen < 31 ? wlen : 31);
        }
    }
    if (strlen(cur) > 0 && n < max_lines) {
        strncpy(lines_out[n], cur, 31); lines_out[n][31] = '\0'; n++;
    }
    if (n == 0) { lines_out[0][0] = '\0'; n = 1; }
    return n;
}


// N-px border — Adafruit_GFX's drawRect is always 1px, so a thicker border
// is N nested rects.
void draw_thick_rect(int x, int y, int w, int h, int thickness, uint16_t color) {
    for (int i = 0; i < thickness; i++) {
        display.drawRect(x + i, y + i, w - 2 * i, h - 2 * i, color);
    }
}

// A field label that inverts (fills ink, text flips to paper) while its
// field is active — the mode is marked at the field, not just by cursor
// position. textSize 2.
void render_field_label(const char* text, int x, int y, bool active) {
    int w = (int)strlen(text) * 12 + 4;
    int h = 16 + 4;
    if (active) {
        display.fillRect(x - 2, y - 2, w, h, BLACK);
        display.setTextColor(WHITE);
    } else {
        display.setTextColor(BLACK);
    }
    display.setTextSize(2);
    display.setCursor(x, y);
    display.print(text);
    display.setTextColor(BLACK);
}


// Boxed '!' + prose paragraph — the alert pattern used by stub screens and
// the discard confirmation: what happened, why, what to do instead, all in
// one paragraph.
void render_alert_icon_body(int top, const char* body) {
    const int icon_size = 46, icon_x = 56;
    display.setTextColor(BLACK);
    draw_thick_rect(icon_x, top, icon_size, icon_size, 2, BLACK);
    display.setTextSize(3);
    display.setCursor(icon_x + (icon_size - 18) / 2, top + (icon_size - 24) / 2);
    display.print("!");

    int text_x = icon_x + icon_size + 22;
    int max_px = 600 - 56 - text_x;
    render_wrapped_lines_at(body, text_x, top, max_px, 34, 3);
}

// Word-wraps text at textSize `ts` within `max_px`, drawing left-aligned
// lines starting at (x, y) with the given line height.
void render_wrapped_lines_at(const char* text, int x, int y, int max_px, int line_h, int ts) {
    const int char_w = 6 * ts;
    int max_chars = max_px / char_w;
    if (max_chars < 1) max_chars = 1;
    char line_buf[80] = "";
    const char* p = text;
    display.setTextSize(ts);
    while (true) {
        const char* ws = p;
        while (*p && *p != ' ') p++;
        int wlen = (int)(p - ws);
        if (wlen > 0) {
            int blen = (int)strlen(line_buf);
            bool fits = (blen == 0) ? (wlen <= max_chars) : (blen + 1 + wlen <= max_chars);
            if (fits) {
                if (blen > 0) strcat(line_buf, " ");
                int avail = 78 - (int)strlen(line_buf);
                strncat(line_buf, ws, avail < wlen ? avail : wlen);
            } else {
                display.setCursor(x, y);
                display.print(line_buf);
                y += line_h;
                memset(line_buf, 0, sizeof(line_buf));
                int avail = 78;
                strncat(line_buf, ws, avail < wlen ? avail : wlen);
            }
        }
        if (*p == '\0') break;
        p++;
    }
    if (strlen(line_buf) > 0) {
        display.setCursor(x, y);
        display.print(line_buf);
    }
}







void render_dial(char* data) {
    // data = "buffer|quick_idx|name|..."
    char buf[24] = "";
    int qidx = -1;
    char* names = NULL;
    char* p1 = strchr(data, '|');
    if (p1) {
        snprintf(buf, sizeof(buf), "%.*s", (int)(p1 - data), data);
        char* p2 = strchr(p1 + 1, '|');
        if (p2) {
            char idx_buf[6] = "";
            snprintf(idx_buf, sizeof(idx_buf), "%.*s", (int)(p2 - p1 - 1), p1 + 1);
            qidx = atoi(idx_buf);
            names = p2 + 1;
        } else {
            qidx = atoi(p1 + 1);
        }
    } else {
        strncpy(buf, data, sizeof(buf) - 1);
    }

    display.setTextColor(BLACK);
    display.setTextSize(2);
    int label_w = 4 * 12; // "DIAL"
    display.setCursor((600 - label_w) / 2, 40);
    display.print("DIAL");

    display.setTextSize(6);
    int buf_w = (int)strlen(buf) * 36, cursor_w = 28;
    int x = (600 - buf_w - cursor_w) / 2;
    display.setCursor(x, 70);
    display.print(buf);
    display.fillRect(x + buf_w, 70, cursor_w, 48, BLACK);
    display.drawLine(0, 170,     600, 170,     BLACK);
    display.drawLine(0, 171,     600, 171,     BLACK);
    display.setTextSize(2);
    display.setCursor(28, 186);
    display.print("RECENT");

    const int row_h = 56;
    int y = 210;
    char* name = names;
    int i = 0;
    while (name != NULL && y + row_h <= 600) {
        char* next = strchr(name, '|');
        char name_buf[24] = "";
        if (next != NULL) {
            int len = (int)(next - name);
            if (len >= (int)sizeof(name_buf)) len = sizeof(name_buf) - 1;
            strncpy(name_buf, name, len);
            name = next + 1;
        } else {
            strncpy(name_buf, name, sizeof(name_buf) - 1);
            name = NULL;
        }
        bool sel = (i == qidx);
        if (sel) {
            display.fillRect(28, y, 600 - 56, row_h, BLACK);
            display.setTextColor(WHITE);
        } else {
            display.setTextColor(BLACK);
        }
        display.setTextSize(3);
        display.setCursor(32, y + (row_h - 24) / 2);
        display.print(name_buf);
        display.setTextColor(BLACK);
        display.drawLine(28, y + row_h - 1, 600 - 28, y + row_h - 1, BLACK);
        i++;
        y += row_h;
    }
}

void render_call_state(char* data) {
    // data = "OUT|IN|ACTIVE" + "|name|timer"
    char state_buf[8] = "", name_buf[32] = "", timer_buf[8] = "00:00";
    char* p1 = strchr(data, '|');
    if (p1) {
        snprintf(state_buf, sizeof(state_buf), "%.*s", (int)(p1 - data), data);
        char* p2 = strchr(p1 + 1, '|');
        if (p2) {
            snprintf(name_buf, sizeof(name_buf), "%.*s", (int)(p2 - p1 - 1), p1 + 1);
            strncpy(timer_buf, p2 + 1, sizeof(timer_buf) - 1);
        } else {
            strncpy(name_buf, p1 + 1, sizeof(name_buf) - 1);
        }
    } else {
        strncpy(state_buf, data, sizeof(state_buf) - 1);
    }

    bool incoming = (strcmp(state_buf, "IN") == 0);
    bool active   = (strcmp(state_buf, "ACTIVE") == 0);
    uint16_t fg = incoming ? WHITE : BLACK;
    if (incoming) display.fillRect(0, 0, 600, 600, BLACK);
    display.setTextColor(fg);

    const char* label = incoming ? "INCOMING CALL" : (active ? "IN CALL" : "CALLING...");
    display.setTextSize(2);
    int label_w = (int)strlen(label) * 12;
    display.setCursor((600 - label_w) / 2, 220);
    display.print(label);

    display.setTextSize(6);
    int name_w = (int)strlen(name_buf) * 36;
    display.setCursor((600 - name_w) / 2, 280);
    display.print(name_buf);

    if (active) {
        display.setTextSize(3);
        int tw = (int)strlen(timer_buf) * 18;
        display.setCursor((600 - tw) / 2, 360);
        display.print(timer_buf);
    }

    const char* hint = incoming ? "ENTER ACCEPT / Q DECLINE" : "Q HANG UP";
    display.setTextSize(2);
    int hint_w = (int)strlen(hint) * 12;
    display.setCursor((600 - hint_w) / 2, 600 - 34 - 16);
    display.print(hint);
    display.setTextColor(BLACK);
}

void setup() {
    Serial.begin(115200);
    delay(2000); 
    Serial.println("\n--- KYPHONE SPI: V4 (NOISE CANCELLER) ---");
    Serial.printf(">> KyPhone firmware %s\n", KYPHONE_VERSION);

    display.begin();
    display.einkOff();

    reclaim_spi_pins_for_gpio();

    pinMode(PIN_SCLK, INPUT_PULLDOWN);
    pinMode(PIN_MOSI, INPUT_PULLDOWN);

    attachInterrupt(digitalPinToInterrupt(PIN_SCLK), spi_clock_isr, RISING);
    // CS ISR disabled: noise on Pin 15 resets bit_counter mid-transfer.
    // SCLK-timeout framing handles message boundaries instead.

    display.expander2.pinMode(8, OUTPUT, true);
    display.expander2.digitalWrite(8, LOW, true);
    delay(10);
    display.expander2.digitalWrite(8, HIGH, true);
    Serial.println(">> Handshake set HIGH");
    Serial.println(">> SYSTEM READY. Send message now.");
}

// Draw one screen command. Called for a command received over SPI from the Radxa,
// and for a "@<command>" line sent over USB serial (the preview below), so a screen
// can be checked on the real panel without the Radxa.
void handle_command(char* text) {
    if (strncmp(text, "HOME_FAST|", 10) == 0) {
        strncpy(current_screen, "HOME", sizeof(current_screen) - 1);
        display.clearDisplay();
        render_home(text + 10);
        display.partialUpdate();
        delay(100);
        display.einkOff();
        reclaim_spi_pins_for_gpio();
    } else if (strncmp(text, "MSG_LIST_FAST|", 14) == 0) {
        strncpy(current_screen, "MSG_LIST", sizeof(current_screen) - 1);
        char* after = text + 14;
        int sel = 0;
        char* idx_end = strchr(after, '|');
        if (idx_end != NULL) {
            char idx_buf[4] = "";
            strncpy(idx_buf, after, idx_end - after);
            sel = atoi(idx_buf);
            after = idx_end + 1;
        }
        display.clearDisplay();
        render_msg_list(after, sel);
        display.partialUpdate();
        delay(100);
        display.einkOff();
        reclaim_spi_pins_for_gpio();
    } else if (ui_is_reader_frame(text)) {
        // Book page: RTEXT frames only draw (they must not clear or refresh); RFOOT ends the page and refreshes.
        strncpy(current_screen, "READER", sizeof(current_screen) - 1);
        int refresh = ui_reader_frame(text);
        if (refresh != UI_READER_NONE) {
            unsigned long now_ms = millis();
            if (refresh == UI_READER_FULL || !did_boot_full_refresh || now_ms - last_full_refresh_ms >= FULL_REFRESH_INTERVAL_MS) {
                display.display();
                last_full_refresh_ms  = now_ms;
                did_boot_full_refresh = true;
                Serial.println(">> Full refresh (reader page)");
            } else {
                display.partialUpdate();
            }
            delay(100);
            display.einkOff();
            reclaim_spi_pins_for_gpio();
        }
    } else {
        if (strncmp(text, "LIGHTSET|", 9) == 0) apply_light(atoi(text + 9));   // the light changes with the drawing
        display.clearDisplay();
        if (ui_dispatch(text, current_screen, sizeof(current_screen))) {
            // OS 0.2.1 screen: drawn by ui_screens.h
        } else if (strncmp(text, "HOME|", 5) == 0) {
            strncpy(current_screen, "HOME", sizeof(current_screen) - 1);
            render_home(text + 5);
        } else if (strncmp(text, "MSG_LIST|", 9) == 0) {
            strncpy(current_screen, "MSG_LIST", sizeof(current_screen) - 1);
            char* after = text + 9;
            int sel = 0;
            char* idx_end = strchr(after, '|');
            if (idx_end != NULL) {
                char idx_buf[4] = "";
                strncpy(idx_buf, after, idx_end - after);
                sel = atoi(idx_buf);
                after = idx_end + 1;
            }
            render_msg_list(after, sel);
        } else if (strncmp(text, "MSG_THREAD|", 11) == 0) {
            char thread_name[32] = "";
            const char* tn = text + 11;
            const char* tn_end = strchr(tn, '|');
            if (tn_end) snprintf(thread_name, sizeof(thread_name), "%.*s", (int)(tn_end - tn), tn);
            else        strncpy(thread_name, tn, sizeof(thread_name) - 1);
            snprintf(current_screen, sizeof(current_screen), "MSG_THREAD(%s)", thread_name);
            render_msg_thread(text + 11);
        } else if (strncmp(text, "LOCK|", 5) == 0) {
            strncpy(current_screen, "LOCK", sizeof(current_screen) - 1);
            render_lock(text + 5);
        } else if (strncmp(text, "DIAL|", 5) == 0) {
            strncpy(current_screen, "DIAL", sizeof(current_screen) - 1);
            render_dial(text + 5);
        } else if (strncmp(text, "CALLSTATE|", 10) == 0) {
            strncpy(current_screen, "CALLSTATE", sizeof(current_screen) - 1);
            render_call_state(text + 10);
        } else {
            strncpy(current_screen, "SMS", sizeof(current_screen) - 1);
            render_sms(text);
        }
        unsigned long now_ms = millis();
        if (!did_boot_full_refresh || now_ms - last_full_refresh_ms >= FULL_REFRESH_INTERVAL_MS) {
            display.display();
            last_full_refresh_ms   = now_ms;
            did_boot_full_refresh  = true;
            Serial.println(">> Full refresh (ghost clear, time-based)");
        } else {
            display.partialUpdate();
        }
        delay(100);
        display.einkOff();
        reclaim_spi_pins_for_gpio();
    }
}

void loop() {
    static uint32_t last_debug = 0;
    uint32_t now = millis();

    // Dev preview: a line "@<command>" on USB serial draws that screen, exactly as if the
    // Radxa had sent it (0xB7 = the '·' sub-field separator). Ignored mid-transfer.
    static char preview[300];
    static int  preview_len = 0;
    while (Serial.available()) {
        int c = Serial.read();
        if (c == '\n') {
            preview[preview_len] = '\0';
            if (preview[0] == '@' && !transfer_complete && bit_counter == 0) {
                Serial.printf(">> PREVIEW: %s\n", preview + 1);
                handle_command(preview + 1);
                Serial.println(">> PREVIEW DONE");
            }
            preview_len = 0;
        } else if (c != '\r' && preview_len < (int)sizeof(preview) - 1) {
            preview[preview_len++] = (char)c;
        }
    }

    // Atomic snapshot — prevents ISR updating last_sclk_time AFTER now_us is captured,
    // which causes unsigned underflow (now_us - last_sclk_time wraps to ~4B > 1500000).
    noInterrupts();
    uint32_t snap_sclk  = last_sclk_time;
    uint32_t snap_first = first_sclk_time;
    uint16_t snap_bits  = bit_counter;
    uint32_t now_us     = micros();
    interrupts();

    if (now - last_debug > 2000) {
        last_debug = now;
        int cs_val = digitalRead(PIN_CS);
        Serial.printf("DEBUG [%lums]: SCLK_Total: %d | Bits: %d | CS: %d | Screen: %s\n",
            millis(), debug_sclk_total, snap_bits, cs_val, current_screen);
    }

    // Framing: a message ends when the clock has been silent for FRAME_SILENCE_US. The Radxa clocks a whole
    // 256-byte frame continuously (about 52 ms at 40 kHz, gaps well under a millisecond), so the silence only has
    // to be clearly longer than the gaps; it was 600 ms, then 150 ms, and every frame of a book page paid it.
    if (!transfer_complete && snap_bits > 0) {
        if (now_us - snap_sclk > FRAME_SILENCE_US) {
            uint32_t elapsed_us = now_us - snap_first;
            if (snap_bits == TOTAL_BITS) {
                transfer_complete = true;
                Serial.printf(">> COMPLETE: %d bits in %lums\n", snap_bits, elapsed_us / 1000);
            } else if (snap_bits > TOTAL_BITS) {
                Serial.printf(">> OVERFLOW: %d bits in %lums. Resetting.\n", snap_bits, elapsed_us / 1000);
                bit_counter = 0;
            } else {
                Serial.printf(">> PARTIAL: %d/%d bits in %lums. Resetting.\n", snap_bits, TOTAL_BITS, elapsed_us / 1000);
                bit_counter = 0;
            }
        }
    }

    if (transfer_complete) {
        display.expander2.digitalWrite(8, LOW, true);
        
        // One byte more than the frame, always 0: a 253-character command fills all 256 bytes
        // and would otherwise have no terminator for the string functions to stop at.
        uint8_t local_buf[PAYLOAD_BYTES + 1];
        memcpy(local_buf, (void*)rx_buf, PAYLOAD_BYTES);
        local_buf[PAYLOAD_BYTES] = 0;
        transfer_complete = false;
        bit_counter = 0;
        
        Serial.println(">> MESSAGE CAPTURED!");

        int offset = -1;
        if (local_buf[0] == FRAME_CHECKED) {
            // A checked frame (0.6.3 on): the CRC-8 of bytes 3..255 must match byte 1, else a bit was flipped on the
            // wire and the frame is dropped rather than drawn wrong.
            uint8_t crc = 0;
            for (int i = 3; i < PAYLOAD_BYTES; i++) {
                crc ^= local_buf[i];
                for (int b = 0; b < 8; b++) crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x07) : (uint8_t)(crc << 1);
            }
            if (crc != local_buf[1] || local_buf[2] != 0x02) {
                Serial.printf(">> CHECKSUM MISMATCH (sent %02X, got %02X). Frame dropped.\n", local_buf[1], crc);
                goto done_processing;
            }
            offset = 2;
        } else {
            for(int i=0; i<PAYLOAD_BYTES; i++) {
                if(local_buf[i] == 0x02 || local_buf[i] == 0x03) { offset = i; break; }
            }
        }

        if (offset != -1) {
            uint8_t marker = local_buf[offset];

            if (marker == 0x03) {
                // --- Display region update (partial refresh) ---
                if (offset + 9 >= PAYLOAD_BYTES) {
                    Serial.println(">> ERROR: 0x03 marker too close to buffer end, skipping");
                    goto done_processing;
                }
                uint8_t flags = local_buf[offset+1];
                bool last_chunk = (flags & 0x01) == 0x00;
                int x = (local_buf[offset+2] << 8) | local_buf[offset+3];
                int y = (local_buf[offset+4] << 8) | local_buf[offset+5];
                int w = (local_buf[offset+6] << 8) | local_buf[offset+7];
                int h = (local_buf[offset+8] << 8) | local_buf[offset+9];
                uint8_t* pixels = &local_buf[offset+10];
                int byte_idx = 0;
                int bit_idx = 7;

                Serial.printf("REGION: (%d,%d) %dx%d last=%d\n", x, y, w, h, last_chunk);

                for (int py = y; py < y + h && byte_idx < PAYLOAD_BYTES; py++) {
                    for (int px = x; px < x + w && byte_idx < PAYLOAD_BYTES; px++) {
                        bool white = (pixels[byte_idx] >> bit_idx) & 0x1;
                        display.drawPixel(px, py, white ? WHITE : BLACK);
                        if (--bit_idx < 0) { bit_idx = 7; byte_idx++; }
                    }
                }

                if (last_chunk) {
                    display.partialUpdate();
                    reclaim_spi_pins_for_gpio();
                }

            } else {
                // --- 0x02: screen command or SMS ---
                char* text = (char*)&local_buf[offset+1];
                Serial.printf("SUCCESS! MSG: %s\n", text);

                handle_command(text);
            }
        } else {
            Serial.println(">> ERROR: No Header (0x02). Check MOSI wiring.");
            Serial.printf("Raw Data: %02X %02X %02X %02X\n", local_buf[0], local_buf[1], local_buf[2], local_buf[3]);
        }

        done_processing:
        display.expander2.digitalWrite(8, HIGH, true);
    }
}