# 智能车图像调试平台 · VisionLab

智能车数据采集、在线调参与原始 C 图像回放的一体化 Windows 桌面 App。两个模块共用同一套无损数据集和实验记录。

## 桌面 App

双击桌面的 `VisionLab` 快捷方式启动，或在本目录运行 `python -m visionlab.app`。应用使用本机 Python、Tkinter、Pillow 和 PySerial，快捷方式指向本工程的窗口模式启动器。移动工程目录后需要重新创建快捷方式。

第一个工作区是“实车工作台”。默认“WiFi / TCP 服务端”模式，电脑监听 `0.0.0.0:8086`，小车作为 TCP 客户端主动连接电脑的 WiFi IPv4。也可切换“串口 / USB CDC”并选择 COM 端口、波特率和协议。图像、遥测、调参、录制与导出共用同一套处理逻辑。在串口模式选择“本地模拟设备”可以验证软件流程。

TCP 服务端代码已接入，本次运行测试由你执行。具体步骤见 [TCP 连接与手动验收](docs/tcp-server-guide.md)。

第二个工作区是“图像实验室”。“选择 C 文件…”可以选择其他文件夹里改好的算法入口，“定位”在资源管理器中选中它。点击“构建并回放”，App 使用所选文件与同目录配套视觉模块编译并顺序执行同一数据集。原图与结果并排显示，右侧集中放置调速、逐帧、区间选帧与导出工具。同一数据集连续运行后，界面显示当前帧相对上次实验的中线变化行数。

“选择帧与导出 AI 调试包”把选定帧原始输入、C 输出、实验源码快照、文字说明和完整前置历史合并成一个 ZIP，并附独立重现脚本。时间轴红点表示异常帧，蓝色区域表示导出选择。详细用法见 [App 接入说明](docs/app-guide.md)。

App 支持 Windows Per-Monitor V2 高 DPI 原生渲染，图像按原始像素整数倍显示。新版已从原有桌面快捷方式启动。

实车旧协议没有处理帧号和图像校验，采集数据会明确标为未验证。对缺少初始化历史的片段，需要显式勾选“允许片段探索”，结果记录为从初始状态重算的探索实验。F5 也提供 `strict` / `exploratory` 模式选择。

实车接入的固件开关、参数单位和协议详情见 [App 接入说明](docs/app-guide.md)。GitHub 调研与采用的优化见 [项目调研记录](docs/github-research.md)。

已接入原工程的 `image.c`、`image2.c`、`ring.c`、`visual_avoidance.c` 和 `laser.c`，CMake 直接编译 `smart-car/project/code/` 中的文件。平台头文件隔离 C251 类型与硬件依赖。

## 当前范围

| 项目 | 状态 |
| --- | --- |
| 原始 C 寻线、左右控制线、中线、参考列输出 | 已实现并测试 |
| 原始环岛状态机连续执行 | 已接入，真实环岛数据待验证 |
| 原始靶点检测 | 可选开启，已运行合成输入测试 |
| 指定帧条件断点、单步、数组、调用栈、硬件监视点 | 已通过真实 GDB 测试 |
| 无损 NumPy 输入、逐字节导出、实验身份记录 | 已实现并测试 |
| OpenCV 查看、播放、逐帧、滑条与 PNG 导出 | 已实现，窗口/滑条创建、渲染、关闭和 PNG 导出已测试 |
| VS Code 调试配置 | 已提供，F5 界面待人工验收 |
| 串口 / USB CDC 上位机接收、录制、调参和导出 | 已实现并测试软件链路，待实车联调 |
| WiFi / TCP 服务端接收、同连接调参、断开后继续监听 | 代码已接入，待手动验收 |
| MCU 稳定快照、编码器事件回放 | 待硬件与后续事件适配 |
| PC 与 MCU 的逐项一致性对比 | 待实车输入及 C251 验证 |

当前回放以新进程的初始状态开始，逐帧调用 `Image_OldStyle_Process(target_enable, 0u)`。视觉避障门控固定为关闭。实车当前使用 `1u`，因此涉及障碍的实车行为仍需要后续事件接入。每次重新运行都会重新初始化全部 C 静态存储状态。

## Windows 快速运行

依赖 Python、NumPy、OpenCV、GCC、MinGW Make、CMake 和 GDB，命令需在 PATH 中可用。本机已用 Python 3.14、GCC 15.2、GDB 16.3、CMake 3.28 实测。

在此目录打开 PowerShell。当前工作区已经包含原工程，重新获取项目时先执行下面两条命令。

```powershell
git clone https://github.com/yunvfeng/smart-car.git smart-car
git -C smart-car checkout 4d7e523b04f982442d2b209c8bacd514b0347a9e
```

构建并生成一段 600 帧的合成数据。输出目录必须是新目录。

```powershell
python -m pip install -r requirements.txt
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build.ps1
python -m visionlab demo datasets/my_demo --frames 600
python -m visionlab run datasets/my_demo runs/my_run --through 527
python -m visionlab view runs/my_run --index 527
```

`--through 527` 执行索引 0 到 527，共 528 帧。索引从零开始，MCU 处理帧号存储在 `frame_ids.npy` 中。省略 `--through` 会执行整个数据集。

查看器中按 A/D 前后逐帧，空格播放或暂停，Q/Esc 退出，滑条选择帧。滑条选择的是已经完成顺序运算的结果。

蓝/橙表示原始左右边线，青/黄表示补线后的左右控制线，绿表示中线，紫表示参考列。控制线与原始边线重合时会覆盖其显示。图像顶部未搜索区域不画线。放大使用最近邻，所有绘图发生在独立显示缓冲区。

导出显示层图像，以及开启原始靶点识别的运行方式如下。

```powershell
python -m visionlab view runs/my_run --index 527 --export runs/my_run/frame527.png
python -m visionlab run datasets/my_demo runs/target_run --target-detect
```

构建脚本在临时目录建立指向工作区的 ASCII junction，解决本机 MinGW 对中文路径的编码问题。源文件仍位于当前工作区。GCC 调试路径通过 UTF-8 response file 映射为相对路径，CMake 使用自身的依赖扫描器，支持重复增量构建。junction 路径在构建日志中显示，并保留供后续构建复用。

## VS Code 与 GDB

1. 用 VS Code 打开本工作区根目录，安装 Microsoft C/C++ 扩展。
2. 在 `smart-car/project/code/image.c` 的 `Search_line()` 函数内部首个赋值行设置断点，当前原工程为第 522 行。
3. 右键断点添加条件 `visionlab_frame_index == 527`。
4. 选择 `VisionLab original C replay`，按 F5。输入数据集路径和最后执行索引，索引至少为 527。
5. 程序先计算所有历史帧，在索引 527 的 `Search_line()` 中暂停。
6. 按 F10 单步，查看 `row_max`、`reference_col`、`left_edge_line`、`right_edge_line`、`mid_line`、`current_step` 和 `mt9v03x_image`。

也可以在 `visionlab_frame_ready()` 设置条件断点，它在装入当前帧后、调用算法前执行。

Debug Console 中可使用下面的 GDB 命令。

```text
-exec print /u left_edge_line[80]@10
-exec print /u mt9v03x_image[0][0]@188
-exec watch left_edge_line[80]
-exec bt
```

自动预启动任务会重新构建，刷新专用的 `build/debug` 会话，并询问严格或探索模式。该目录中的三份生成文件会被替换，历史实验保存在各自的 `runs/` 目录。要保留本次调试结果，先复制 `build/debug` 到独立目录。调试器中断进程时，JSONL 可能仅含已经刷新的完整行。

独立验证 GDB 可运行下面命令。测试实际执行到索引 527，检查原始源码、单步后的局部变量、图像/边线数组、调用栈与写监视点，日志保存在 `build/gdb-smoke.log`。

```powershell
python scripts/check_gdb.py
```

## 数据集契约

```text
dataset/
  frames.npy       uint8, shape (N,120,188)
  frame_ids.npy    shape (N,), integer processed-frame IDs
  metadata.json
```

最小元数据示例如下。

```json
{
  "schema_version": 1,
  "source": "synthetic",
  "initial_state": "process_start",
  "external_events": "none"
}
```

真实采集数据应记录其来源、曝光、固件版本与完整处理帧历史。`initial_state` 必须为 `process_start`，意味着从视觉状态初始化开始保留全部历史。本阶段尚未支持中途片段的状态恢复。不能通过修改元数据来补足缺失的历史。

加载器校验尺寸、类型、顺序、重复 ID 和空数据，严格回放拒绝执行范围内有处理帧号缺口的数据集。存在 `events.bin` 时会报错，待事件适配完成后接入。输入直接导出连续灰度字节，每帧 22,560 字节。C 程序在每次调用前后比较输入缓冲区，若算法改写原图则失败。

每个运行目录包含 `frames.bin`、`results.jsonl` 和 `run.json`。JSONL 每帧记录五条完整数组、参考列、阈值、丢线行、环岛方向/状态和靶点。`run.json` 保存图像与帧号 SHA-256、原工程提交、构建源码 SHA-256、可执行文件身份、编译器和完整运行参数。源码或二进制发生变化后必须重新构建。失败的运行保留已有输出供诊断，状态写为 `failed`。

## 测试

```powershell
python -m unittest discover -s tests -v
python scripts/check_gdb.py
```

测试覆盖输入逐字节保存、顺序前缀、跨进程确定性、数组边界、中线关系、黑白/噪声帧、靶点启用、输入异常、帧号缺口、初始状态、已有结果保护与截断输入。合成测试验证软件链路。真实赛道识别质量、DMA 一致性与 MCU 计算结果仍需实测。

Keil 原工程的本次编译尚未执行。原仓库中的历史零错误日志仅用于识别基线。

## 后续采集接入

DMA 库实现、原图叠加写入、USB 部分发送和外部事件的核查结果，以及下一阶段的接入条件，见 [源码核查记录](docs/source-audit.md)。

OpenImageDebugger 和 Matrix Viewer 的选型记录及待验收项目也列于该文档。
