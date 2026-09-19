// Plays book pages through the firmware's own reader code (ui_reader.h) on a computer.
//
//   render_reader OUTDIR < pages.txt      (OUTDIR = - : play the frames but write no images)
//
// Each input line is  <name><TAB><frame><TAB><frame>...  (one page: its RTEXT frames, then its RFOOT; byte 0xB7 = the
// sub-field separator). The frames are played in order against one display, exactly as the firmware receives them.
// For every line it prints  <name><TAB><refresh>  where refresh is what the last frame asked of the panel (0 none,
// 1 partial, 2 full), and writes OUTDIR/<name>.raw: 600x600 bytes, 0 = ink, 255 = paper.
#include "mock_display.h"
MockDisplay display;
#include "../../Inkplate_SPI_Peripheral/ui_reader.h"
#include <string>

int main(int argc, char** argv) {
    if (argc < 2) { fprintf(stderr, "usage: render_reader OUTDIR < pages\n"); return 2; }
    std::string outdir = argv[1];
    static char line[16384];
    int pages = 0;
    while (fgets(line, sizeof(line), stdin)) {
        size_t len = strlen(line);
        while (len && (line[len - 1] == '\n' || line[len - 1] == '\r')) line[--len] = '\0';
        char* tab = strchr(line, '\t');
        if (!tab) continue;
        *tab = '\0';
        std::string name = line;
        display.clearDisplay();
        int refresh = UI_READER_NONE;
        for (char* frame = tab + 1; frame; ) {
            char* next = strchr(frame, '\t');
            if (next) *next = '\0';
            char buf[512];
            snprintf(buf, sizeof(buf), "%.253s", frame);          // the firmware's receive buffer: at most 253 chars + NUL
            if (ui_is_reader_frame(buf)) refresh = ui_reader_frame(buf);
            frame = next ? next + 1 : NULL;
        }
        printf("%s\t%d\n", name.c_str(), refresh);
        if (outdir != "-") {
            std::string path = outdir + "/" + name + ".raw";
            FILE* f = fopen(path.c_str(), "wb");
            if (!f) { fprintf(stderr, "cannot write %s\n", path.c_str()); return 1; }
            static uint8_t out[600 * 600];
            for (int i = 0; i < 600 * 600; i++) out[i] = display.px[i] ? 0 : 255;
            fwrite(out, 1, sizeof(out), f);
            fclose(f);
        }
        pages++;
    }
    fprintf(stderr, "played %d pages\n", pages);
    return 0;
}
