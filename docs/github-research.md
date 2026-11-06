# GitHub 调研与本次优化

调研日期为 2026-11-06。以下结论依据项目公开仓库和说明，当前实现独立编写。

| 项目 | 相关能力 | 本次采用的设计 |
| --- | --- | --- |
| Serial Studio | 串口设备配置、遥测仪表、CSV 导出、回放 | 将连接、实时指标、图像、曲线和会话导出集中到实车工作区 |
| PyQt-multifunctional-uart-helper | 智能车图像接收、参数列表和设备调参 | 图像与参数分区，参数通道以当前固件源码为依据 |
| PlotJuggler | 实时时序数据和离线时间序列查看 | 实时遥测保留滚动历史，原始接收流与 CSV 同时保存 |
| Homo_Vision_Public | 录像、逐帧回放、运行到指定帧再显示 | 回放始终从索引零推进，显示层可自由选择已计算结果 |
| OpenImageDebugger | GDB/LLDB 内存图像查看、VS Code 集成 | 保留真实 GDB 源码调试接口，以数据集和帧索引连接 App 与编辑器 |

Serial Studio 的公开说明同时包含 GPL 与 Pro 功能边界，其中部分输出和图像组件属于 Pro。本项目采用独立桌面实现，具体功能按本工程测试结果交付。[Serial Studio](https://github.com/Serial-Studio/Serial-Studio)

智能车 PyQt 串口助手展示了参数表、图像与波形的组合。其 README 使用的字符串和图像帧头协议与本固件不同，本次按照 `seekfree_assistant.c/.h` 核对字节序、校验和与通道编号。[PyQt-multifunctional-uart-helper](https://github.com/IronSublimate/PyQt-multifunctional-uart-helper)

PlotJuggler 专注时间序列查看，Homo_Vision_Public 描述了先计算到指定帧的回放方式。本 App 将接收与离线运行分成两个模块，以录制目录作为交接点。[PlotJuggler](https://github.com/PlotJuggler/PlotJuggler) · [Homo_Vision_Public](https://github.com/hilookas/Homo_Vision_Public)

OpenImageDebugger 提供内存图像查看，Windows 支持标为实验性。本次保留为可选工具，已验证的主调试链使用 GCC 调试信息和 GDB。[OpenImageDebugger](https://github.com/OpenImageDebugger/OpenImageDebugger)

## 已落实的优化

- 串口由后台线程独占，设备收发、录制和参数命令按队列处理。
- 图像预览只保留最新快照，曲线使用固定长度历史。录制发生在 UI 刷新之前，界面降帧时仍保存接收图像。
- 逐飞解析器支持拆包、粘包、图像内出现帧头字节、遥测校验与流重同步。
- 提供 CRC32 图像封装的扩展接收模式，并明确区分其传输校验和 MCU 输入快照验证。
- 参数范围、有限值和协议校验在发送前检查。当前固件无 ACK，界面显示发送状态。
- 会话保存原始字节流、无损帧、帧号、遥测和参数事件，ZIP 导出保留这些文件。
- 捕获数据可直接转入原始 C 回放，实验中记录探索模式、源码/可执行文件身份与数据哈希。
- 新进程运行完整历史，异常帧标记和上次实验比较服务于人工定位。
- 窗口启动器、日志、图标和桌面快捷方式统一启动两个模块。
