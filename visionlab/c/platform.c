#include "zf_common_headfile.h"
#include "servo.h"
#include <stdio.h>
#include <stdlib.h>

uint8 mt9v03x_image[MT9V03X_H][MT9V03X_W];
volatile uint8 mt9v03x_finish_flag;
uint8 EA = 1;
/* Observable platform outputs. No actuator control loop runs on the PC. */
servo_mode_enum visionlab_servo_mode = SERVO_MODE_VISION;
int16 visionlab_path_bias;

void Servo_Set_Mode(servo_mode_enum mode) { visionlab_servo_mode = mode; }
void Servo_Set_Path_Bias(int16 bias) { visionlab_path_bias = bias; }

static void unsupported_camera(void)
{
    fputs("Live camera/exposure calls are unsupported during replay.\n", stderr);
    exit(EXIT_FAILURE);
}
void system_delay_ms(uint16 ms) { (void)ms; unsupported_camera(); }
uint8 mt9v03x_set_exposure_time(uint16 exposure)
{
    (void)exposure;
    unsupported_camera();
    return 1;
}
/* These only satisfy the unused laser hardware functions in original laser.c. */
void gpio_init(int pin, int direction, int level, int mode)
{
    (void)pin; (void)direction; (void)level; (void)mode;
}
void gpio_set_level(int pin, int level) { (void)pin; (void)level; }
