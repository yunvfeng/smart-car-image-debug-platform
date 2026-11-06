#ifndef VISIONLAB_PLATFORM_H
#define VISIONLAB_PLATFORM_H
#include <stdint.h>
#include <stddef.h>
#include <string.h>

typedef uint8_t uint8;
typedef uint16_t uint16;
typedef uint32_t uint32;
typedef int8_t int8;
typedef int16_t int16;
typedef int32_t int32;
typedef uint8_t bit;
typedef int pwm_channel_enum;
#define far

#define MT9V03X_H 120
#define MT9V03X_W 188
#define MT9V03X_EXP_TIME_DEF 512
#define IO_P61 0
#define IO_P63 1
#define IO_P66 2
#define GPO 0
#define GPO_PUSH_PULL 0

extern uint8 mt9v03x_image[MT9V03X_H][MT9V03X_W];
extern volatile uint8 mt9v03x_finish_flag;
extern uint8 EA;
void system_delay_ms(uint16 ms);
uint8 mt9v03x_set_exposure_time(uint16 exposure);
void gpio_init(int pin, int direction, int level, int mode);
void gpio_set_level(int pin, int level);
#endif
