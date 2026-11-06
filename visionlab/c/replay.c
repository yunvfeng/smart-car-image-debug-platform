#include "image.h"
#include "ring.h"
#include "laser.h"
#include "visual_avoidance.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>

/* Zero-based dataset index, independent of the MCU's processed-frame ID. */
long long visionlab_frame_index = 0;

/* A stable breakpoint after loading the input and before running original C. */
void visionlab_frame_ready(void)
{
    volatile long long index = visionlab_frame_index;
    (void)index;
}

static void array_json(FILE *out, const char *name, const uint8 *values)
{
    unsigned row;
    fprintf(out, ",\"%s\":[", name);
    for (row = 0; row < SEARCH_IMAGE_H; ++row) {
        fprintf(out, "%s%u", row ? "," : "", (unsigned)values[row]);
    }
    fputc(']', out);
}

static int write_result(FILE *out)
{
    fprintf(out, "{\"index\":%lld,\"reference_col\":%u,\"white_min\":%u,"
            "\"white_max\":%u,\"lost_left\":%u,\"lost_right\":%u,"
            "\"ring_step\":%u,\"ring_l\":%u,\"ring_r\":%u,"
            "\"target\":%u,\"target_x\":%u,\"target_y\":%u",
            visionlab_frame_index, reference_col, white_min_point, white_max_point,
            lost_left, lost_right, current_step, ring_l, ring_r, tar_flag, tar_x, tar_y);
    return !ferror(out);
}

static int parse_index(const char *text, long long *value)
{
    char *end;
    errno = 0;
    *value = strtoll(text, &end, 10);
    return !errno && *text && !*end && *value >= 0;
}

int main(int argc, char **argv)
{
    FILE *input;
    FILE *output;
    size_t count;
    size_t processed = 0;
    long long through = -1;
    uint8 original[MT9V03X_H][MT9V03X_W];
    uint8 target_enable = 0;
    int status = EXIT_FAILURE;

    if (argc < 3 || argc > 5 ||
        (argc >= 4 && !parse_index(argv[3], &through)) ||
        (argc == 5 && strcmp(argv[4], "0") && strcmp(argv[4], "1"))) {
        fputs("Usage: visionlab_replay frames.bin results.jsonl [through_index [target_0_or_1]]\n", stderr);
        return EXIT_FAILURE;
    }
    if (!strcmp(argv[1], argv[2])) {
        fputs("Input and output paths must differ.\n", stderr);
        return EXIT_FAILURE;
    }
    if (argc == 5) target_enable = (uint8)(argv[4][0] == '1');
    input = fopen(argv[1], "rb");
    if (!input) { perror(argv[1]); return EXIT_FAILURE; }
    /* Exclusive creation protects existing runs and hard-linked inputs. */
    output = fopen(argv[2], "wbx");
    if (!output) { perror(argv[2]); fclose(input); return EXIT_FAILURE; }
    VisualAvoid_Init();
    while ((count = fread(mt9v03x_image, 1, sizeof(mt9v03x_image), input)) != 0) {
        if (count != sizeof(mt9v03x_image)) {
            fputs("Truncated raw frame.\n", stderr);
            goto done;
        }
        memcpy(original, mt9v03x_image, sizeof(original));
        visionlab_frame_ready();
        /* Phase one has no recorded encoder ticks. Avoidance is explicitly off. */
        Image_OldStyle_Process(target_enable, 0u);
        if (memcmp(original, mt9v03x_image, sizeof(original))) {
            fputs("Algorithm modified raw input.\n", stderr);
            goto done;
        }
        if (!write_result(output)) goto done;
        /* Append arrays without seeking, so output also works on streaming files. */
        array_json(output, "left", left_edge_line);
        array_json(output, "right", right_edge_line);
        array_json(output, "left_control", left_control_line);
        array_json(output, "right_control", right_control_line);
        array_json(output, "mid", mid_line);
        fputs("}\n", output);
        if (ferror(output)) goto done;
        ++processed;
        if (visionlab_frame_index == through) break;
        ++visionlab_frame_index;
    }
    if (ferror(input)) { perror("Reading frames"); goto done; }
    if (!processed || (through >= 0 && (unsigned long long)through != processed - 1)) {
        fputs("Empty input or requested index outside dataset.\n", stderr);
        goto done;
    }
    status = EXIT_SUCCESS;
done:
    if (fclose(output)) status = EXIT_FAILURE;
    fclose(input);
    return status;
}
