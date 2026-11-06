# 源码核查与阶段边界

## 版本与实际编译路径

本次获取的原工程提交为 `4d7e523b04f982442d2b209c8bacd514b0347a9e`。原工程保存在独立的 `smart-car/` checkout，VisionLab 根仓库通过 `.gitignore` 排除该 checkout。当前固件 checkout 的差异为空。

`project/mdk/seekfree.uvproj` 的目标是 `stc32g144k246`，工具集为 MCS-251，设备字段为 AI8052U-32Bit Series。工程列出 113 个活跃文件条目，业务入口为 `project/code/main.c`，中断文件为 `project/user/isr.c`，启动汇编为 `libraries/zf_common/START251.A51`。`project/user/main.c` 未列入该目标。

原工程包含寻线、环岛、视觉避障、靶点、助手及底层 USB 源码，同时链接 `zf_device_mt9v03x_dma.LIB`。现有历史构建日志记录 0 errors / 0 warnings，程序尺寸为 data=10.4、edata+hdata=8204、xdata=32562、const=5761、code=126670。这些数字来自仓库历史产物，本次 PC 构建不更新它们。

## 输入与调用链

主循环观察 `mt9v03x_finish_flag` 后清零，再调用 `Image_OldStyle_Process(target_detect_enabled, 1u)`。发车前在每个图像周期调用 `Gyro_Update()`，发车后由 15 ms Timer0 回调执行编码器、`VisualAvoid_ControlTick()`、陀螺仪、舵机、电机和激光任务。README 的部分激光门控描述与当前 `main.c` 有差异，本次以源码为准。

视觉入口先取 `&mt9v03x_image[0][0]`，依次更新底部阈值、参考列、原始边线和控制线，然后执行 `Ring()` 与 `Fitted_Midline()`。靶点识别由门控和静态隔帧计数器调度，直接读取同一全局图像。Otsu 的当前编译宏为零，`image2.c` 保留在 PC 编译目标中。

`Search_reference_col()` 存在函数静态 `reference_ready`，`image.c` 有反光阈值历史和靶点计数器，`ring.c` 有多组状态确认计数器。仅清空导出的全局数组无法完整恢复初始状态。当前采用新进程启动，每个数据集从索引零顺序执行。

## 最小 PC 适配

| 边界 | 适配方式 |
| --- | --- |
| 原工程 `zf_common_headfile.h` | PC include 路径优先匹配专用同名头文件 |
| `uint8/16/32` 与有符号类型 | 使用 `stdint.h` 的固定位宽类型 |
| C251 `far` | PC 下展开为空 |
| C251 `bit` / EA | 单线程 PC 下用 `uint8_t` 和模拟 EA |
| 全局摄像头数组 | PC 拥有完整静态 120×188 缓冲区 |
| 曝光和延时 | 调用时报错并终止，避免引入隐式输入变化 |
| 舵机模式/路径偏置 | 仅保存平台输出值 |
| 激光 GPIO | 空实现，用于链接原文件中的硬件函数 |
| 电机/舵机/PID 循环 | PC 目标未链接这些执行器实现 |

视觉核心文件直接作为 CMake 编译输入。环岛代码真实执行，避障代码真实编译，靶点代码真实编译且可开启。当前避障门控为零，因为 `VisualAvoid_ControlTick()` 的编码器里程和回正周期历史尚未输入。PC 的普通 `int` 为 32 位，C251 原始 typedef 将 `int` 作为 16 位使用，整数提升和溢出行为需要阶段五专项对照。

## DMA 稳定快照尚待核实

`zf_device_mt9v03x.c` 将唯一图像地址传给 `mt9v03x_dma_init()`，头文件公开 VSYNC 和 DMA handler 声明，其关键定义来自预编译 `.LIB`。当前公开 C 源码未显示该缓冲区的写入生命周期、DMA 停止时刻、下一次采集是否等待处理释放，也未显示可用的 acquire/release 接口。

因此不能从 finish 标志推断整个处理期间缓冲区保持不变。即使在入口执行一次 `memcpy`，复制期间也可能发生 DMA 更新。需要取得对应 DMA 实现或厂商的缓冲区所有权约定，并在板上观测验证。

阶段二应使算法与记录器使用同一份已经冻结、所有权明确的帧。记录处理帧号、采集帧号、会话号和丢弃统计，完整记录所有实际进入算法的帧。短片段录制需要从可重现初始状态开始，或同时提供完整状态快照和此前事件。当前回放器会拒绝不完整的处理历史。

## 助手叠加与 USB 发送

`assistant_debug.h` 默认关闭整个助手。若未来启用，其图像叠加默认开启，`assistant_debug.c` 的像素/文字函数直接修改 `mt9v03x_image`。原始采集必须在独立显示副本上叠加，并避免让这条路径写入算法快照。当前 VisionLab 显示端已使用独立图像缓冲。

`usb_cdc_write_buffer()` 返回剩余未确认发送的长度，设备未配置时直接返回原长度。实现按 64 字节写入端点，再等待完成或超时。超时时当前包已经进入 FIFO，剩余长度包含尚未得到确认的包，因此直接重试残余区间还存在重复数据风险。采集协议应以帧级长度、序号和校验处理失败与重同步，不能把非零返回视为成功，也不能据此证明端点中的包绝未到达。

100 FPS 原始输入需要每秒 2,256,000 个图像字节，加上协议和事件开销。需要实测实际处理帧率、USB 吞吐和最长阻塞。采样掉帧后，即使剩余图像无损，也无法完整复现未记录帧推动的状态机。处理序号必须在实际进入视觉算法时分配。

## 下一阶段待验收

- 获取并确认 DMA 的缓冲区释放/暂停机制，验证复制或冻结期间数据稳定。
- 在原 Keil 目标加入明确的采集模块，检查额外帧快照的 22,560 字节内存占用、map 和程序尺寸。
- 使用带版本、会话、处理帧号、固定尺寸、长度及校验的帧协议，实现 PySerial 流式接收、断包重同步、停止录制和无损落盘。
- 测试断连、部分发送、CRC 错误、帧号缺口、重启以及尾部半包。
- 对比启用前后的图像处理周期、15 ms 控制抖动与 USB 阻塞时间。
- 按实际发生顺序记录编码器 tick、门控和在线参数变化，定义事件回放边界。
- 对同一输入、初始化状态与事件，在 PC 和 MCU 比较原始边线、控制线、中线与环岛转移。

## 参考工具核查

OpenImageDebugger 官方说明提供 GDB/LLDB 内存缓冲区查看和 VS Code 集成，Windows 支持标注为实验性。可作为当前 `mt9v03x_image` 的断点图像查看器，本次尚未安装或执行插件级验收。[OpenImageDebugger](https://github.com/OpenImageDebugger/OpenImageDebugger)

Matrix Viewer Debug 官方说明支持 cppdbg/GDB、数值数组的 1D 图表及二维图像，提供调试侧栏和变量右键入口。后续可用 `left_edge_line`、`right_edge_line`、`mid_line` 和 `mt9v03x_image` 验证 C 数组的实际识别效果。本次仅核查文档，实际已测试的数组查看使用 GDB。[Matrix Viewer Debug](https://github.com/btsd321/matrix_viewer_debug)

Homo_Vision_Public 的 README 描述了运行到指定帧后再显示的回放模式。本项目采用相同的顺序推进思路。此次网页服务未能返回 `src/cap.c`、`src/cap.h` 与 `src/main.cpp` 的正文，这部分逐文件审阅仍待补充。[Homo_Vision_Public](https://github.com/hilookas/Homo_Vision_Public)

Wheel-legged-autocar 的仓库文档列出 PC 端检测器与车载视频离线工具。指定 `c_pvc_detector` 目录此次未取得正文，当前实现独立编写，后续接入复杂实验比较时再核查其实现与许可。[Wheel-legged-autocar](https://github.com/boatchanting/Wheel-legged-autocar)

Intelligentcar2022-FZ3B 已核查项目入口，分阶段图像展示的具体源码研究仍待后续阶段。[Intelligentcar2022-FZ3B](https://github.com/Leo-smart/Intelligentcar2022-FZ3B)

以上第三方工具均未加入必需运行依赖。第一阶段的数据集、C 可执行文件、GDB 和 OpenCV 查看器形成可独立运行的链路。
