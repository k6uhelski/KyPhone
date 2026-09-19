// Renders screens with the firmware's own renderers (ui_screens.h) on a computer.
//
//   render_host OUTDIR < commands.txt      (OUTDIR = - : render but write nothing)
//
// Each input line is  <name><TAB><wire command>  (byte 0xB7 = the sub-field separator).
// Writes OUTDIR/<name>.raw: 600x600 bytes, 0 = ink, 255 = paper.
#include "mock_display.h"
MockDisplay display;
#include "../../Inkplate_SPI_Peripheral/ui_screens.h"
#include <string>

int main(int argc, char** argv) {
    if (argc < 2) { fprintf(stderr, "usage: render_host OUTDIR < commands\n"); return 2; }
    std::string outdir = argv[1];
    char line[1024];
    int rendered = 0, unknown = 0;
    while (fgets(line, sizeof(line), stdin)) {
        size_t len = strlen(line);
        while (len && (line[len - 1] == '\n' || line[len - 1] == '\r')) line[--len] = '\0';
        char* tab = strchr(line, '\t');
        if (!tab) continue;
        *tab = '\0';
        char* wire = tab + 1;
        char buf[512];
        snprintf(buf, sizeof(buf), "%s", wire);       // the firmware's receive buffer is at most 253 chars + NUL
        display.clearDisplay();
        char screen[32];
        if (!ui_dispatch(buf, screen, sizeof(screen))) { fprintf(stderr, "unknown command: %s\n", wire); unknown++; continue; }
        if (outdir == "-") { rendered++; continue; }        // "-" = render only (fuzzing): no output files
        std::string path = outdir + "/" + line + ".raw";
        FILE* f = fopen(path.c_str(), "wb");
        if (!f) { fprintf(stderr, "cannot write %s\n", path.c_str()); return 1; }
        static uint8_t out[600 * 600];
        for (int i = 0; i < 600 * 600; i++) out[i] = display.px[i] ? 0 : 255;
        fwrite(out, 1, sizeof(out), f);
        fclose(f);
        rendered++;
    }
    printf("rendered %d, unknown %d\n", rendered, unknown);
    return unknown ? 1 : 0;
}
