// A fake Inkplate display for building the firmware's screen renderers on a
// computer: the subset of Adafruit_GFX that ui_screens.h uses, drawn into a
// 600x600 one-byte-per-pixel buffer (1 = ink), with the real GFX 5x7 font.
#pragma once
#include <stdint.h>
#include <string.h>
#include <stdio.h>

#define BLACK 1
#define WHITE 0
#define PROGMEM
#define pgm_read_byte(addr)   (*(const uint8_t*)(addr))
#define pgm_read_word(addr)   (*(const uint16_t*)(addr))
#include GLCDFONT_C          // Adafruit_GFX's glcdfont.c: `static const unsigned char font[]`
#include GFXFONT_H           // Adafruit_GFX's gfxfont.h: GFXglyph and GFXfont (what the FreeSerif headers use)

struct MockDisplay {
    uint8_t px[600 * 600];
    int cx = 0, cy = 0, ts = 1;
    uint16_t tc = BLACK;
    bool wrap = true;
    const GFXfont* gfxFont = nullptr;      // nullptr = the built-in 5x7 font

    void clearDisplay() { memset(px, 0, sizeof(px)); cx = cy = 0; }
    void setCursor(int x, int y) { cx = x; cy = y; }
    void setTextSize(int s) { ts = s; }
    void setTextColor(uint16_t c) { tc = c; }
    void setTextWrap(bool w) { wrap = w; }

    // Adafruit_GFX::setFont: switching between the built-in and a custom font shifts the cursor by 6 (a
    // custom font's cursor y is the baseline).
    void setFont(const GFXfont* f) {
        if (f) { if (!gfxFont) cy += 6; }
        else if (gfxFont) cy -= 6;
        gfxFont = f;
    }

    void drawPixel(int x, int y, uint16_t c) {
        if (x < 0 || y < 0 || x >= 600 || y >= 600) return;
        px[y * 600 + x] = (c == BLACK) ? 1 : 0;
    }
    void fillRect(int x, int y, int w, int h, uint16_t c) {
        for (int yy = y; yy < y + h; yy++) for (int xx = x; xx < x + w; xx++) drawPixel(xx, yy, c);
    }
    void drawRect(int x, int y, int w, int h, uint16_t c) {
        if (w <= 0 || h <= 0) return;
        for (int xx = x; xx < x + w; xx++) { drawPixel(xx, y, c); drawPixel(xx, y + h - 1, c); }
        for (int yy = y; yy < y + h; yy++) { drawPixel(x, yy, c); drawPixel(x + w - 1, yy, c); }
    }
    void drawLine(int x0, int y0, int x1, int y1, uint16_t c) {           // horizontal / vertical only
        if (y0 == y1) for (int x = x0; x <= x1; x++) drawPixel(x, y0, c);
        else          for (int y = y0; y <= y1; y++) drawPixel(x0, y, c);
    }

    // Adafruit_GFX::drawBitmap: 1 bits are drawn in `color`, 0 bits are left alone.
    void drawBitmap(int x, int y, const uint8_t* bitmap, int w, int h, uint16_t color) {
        int byte_width = (w + 7) / 8;
        for (int j = 0; j < h; j++)
            for (int i = 0; i < w; i++)
                if (bitmap[j * byte_width + i / 8] & (128 >> (i & 7))) drawPixel(x + i, y + j, color);
    }

    // Adafruit_GFX::drawChar with the classic font, transparent background.
    void drawChar(int x, int y, unsigned char c, uint16_t color, int size) {
        for (int8_t i = 0; i < 5; i++) {
            uint8_t line = font[c * 5 + i];
            for (int8_t j = 0; j < 8; j++, line >>= 1) {
                if (line & 1) {
                    if (size == 1) drawPixel(x + i, y + j, color);
                    else           fillRect(x + i * size, y + j * size, size, size, color);
                }
            }
        }
    }
    // Adafruit_GFX::drawChar, custom-font branch (text size 1): glyph bitmap at (cursor + xOffset, baseline + yOffset).
    void drawGlyph(int x, int y, unsigned char c, uint16_t color) {
        c -= (uint8_t)pgm_read_byte(&gfxFont->first);
        const GFXglyph* glyph = &gfxFont->glyph[c];
        const uint8_t* bitmap = gfxFont->bitmap;
        uint16_t bo = pgm_read_word(&glyph->bitmapOffset);
        uint8_t w = pgm_read_byte(&glyph->width), h = pgm_read_byte(&glyph->height);
        int8_t xo = pgm_read_byte(&glyph->xOffset), yo = pgm_read_byte(&glyph->yOffset);
        uint8_t bits = 0, bit = 0;
        for (uint8_t yy = 0; yy < h; yy++) {
            for (uint8_t xx = 0; xx < w; xx++) {
                if (!(bit++ & 7)) bits = pgm_read_byte(&bitmap[bo++]);
                if (bits & 0x80) drawPixel(x + xo + xx, y + yo + yy, color);
                bits <<= 1;
            }
        }
    }
    // Adafruit_GFX::write, custom-font branch (text size 1).
    void writeCustom(uint8_t c) {
        if (c == '\n') { cx = 0; cy += (uint8_t)pgm_read_byte(&gfxFont->yAdvance); }
        else if (c != '\r') {
            uint8_t first = pgm_read_byte(&gfxFont->first);
            if (c >= first && c <= (uint8_t)pgm_read_byte(&gfxFont->last)) {
                const GFXglyph* glyph = &gfxFont->glyph[c - first];
                uint8_t w = pgm_read_byte(&glyph->width), h = pgm_read_byte(&glyph->height);
                if (w > 0 && h > 0) {
                    int xo = (int8_t)pgm_read_byte(&glyph->xOffset);
                    if (wrap && (cx + (xo + w)) > 600) { cx = 0; cy += (uint8_t)pgm_read_byte(&gfxFont->yAdvance); }
                    drawGlyph(cx, cy, c, tc);
                }
                cx += (uint8_t)pgm_read_byte(&glyph->xAdvance);
            }
        }
    }
    // Adafruit_GFX::write: wraps at the panel edge, like the real display.
    void write(uint8_t c) {
        if (gfxFont) { writeCustom(c); return; }
        if (c == '\n') { cx = 0; cy += ts * 8; }
        else if (c != '\r') {
            if (wrap && (cx + ts * 6) > 600) { cx = 0; cy += ts * 8; }
            drawChar(cx, cy, c, tc, ts);
            cx += ts * 6;
        }
    }
    void print(const char* s) { for (; *s; s++) write((uint8_t)*s); }
};
