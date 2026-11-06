# 智能车图像调试平台 · VisionLab

面向智能车视觉算法开发的 Windows 桌面工具，将实车数据采集、在线调参与 C 算法回放整合在一个 App 中。

| 模块 | 功能 |
| --- | --- |
| 实车工作台 | WiFi / TCP 服务端、串口接入、图像与遥测显示、在线调参、无损录制和数据导出 |
| 图像实验室 | 选择外部 C 源码、自动编译回放、原图与结果对照、播放调速、异常标记、区间选帧和 AI 调试包导出 |

## 下载使用

在 [Releases](https://github.com/yunvfeng/smart-car-image-debug-platform/releases) 下载 Windows 64 位便携包，完整解压到可写目录，双击 `VisionLab.exe` 启动。运行环境、默认视觉源码和 C 编译工具已随包提供。首次回放会自动编译算法。

## 从源码运行

### 1. 准备环境

安装 Python 3（含 Tkinter）、Git、GCC、MinGW Make 和 CMake 3.20 及以上版本，将 `python`、`git`、`gcc`、`mingw32-make`、`cmake` 加入 PATH。需要 VS Code 单步调试时，再安装 GDB 和 Microsoft C/C++ 扩展。

### 2. 获取项目并启动

在 PowerShell 中执行以下命令。`smart-car` 提供默认视觉算法及其配套模块。

```powershell
git clone https://github.com/yunvfeng/smart-car-image-debug-platform.git
cd smart-car-image-debug-platform
git clone https://github.com/yunvfeng/smart-car.git smart-car
git -C smart-car checkout 4d7e523b04f982442d2b209c8bacd514b0347a9e
python -m pip install -r requirements.txt
python -m visionlab.app
```

需要桌面快捷方式时，在项目目录执行以下命令，之后双击桌面的 **VisionLab** 即可启动。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_shortcut.ps1
```

### 3. 跑通第一次回放

1. 打开 **图像实验室**，点击 **生成测试数据**。
2. 保持默认视觉源码，点击 **构建并回放**，完成首次编译与计算。
3. 对照原始输入和 C 算法输出，使用右侧工具调速、逐帧查看或跳转索引。

## 连接小车与采集数据

### WiFi / TCP

1. 将电脑接入小车所在的 WiFi 网络。
2. 在 **实车工作台** 选择 **WiFi / TCP 服务端**，默认监听 `0.0.0.0:8086`。
3. 在小车端填写电脑的 WiFi IPv4 地址和端口 `8086`，由小车作为 TCP 客户端连接。
4. 选择与固件匹配的协议，点击 **开始监听**，查看实时图像和遥测曲线。

### 串口 / USB CDC

选择 **串口 / USB CDC**，刷新并选择 COM 端口，设置波特率与协议，然后点击 **连接设备**。选择 **本地模拟设备** 可体验采集流程。

连接后，在参数旁点击 **发送** 下发数值。点击 **开始录制**，结束后点击 **停止并保存**，随后可导出会话 ZIP，或点击 **送往图像实验室 →** 继续分析。固件配置与通信说明见 [TCP 接入说明](docs/tcp-server-guide.md) 和 [使用指南](docs/app-guide.md)。

## 使用自己的视觉源码

1. 在 **图像实验室** 选择数据集。
2. 点击 **选择 C 文件…**，选取任意文件夹中修改好的算法文件。
3. 点击 **构建并回放**，使用新源码处理图像。
4. 再次修改源码后，重新点击 **构建并回放** 更新结果。

算法沿用默认工程的视觉接口，入口函数为 `Image_OldStyle_Process`，输入为 `188 × 120` 的灰度图。入口文件与配套视觉模块可放在同一目录。**定位** 可在资源管理器中选中文件，**编辑源码** 可打开 VS Code。

## 选帧与导出 AI 调试包

1. 播放时点击 **标记异常帧**，时间轴对应位置出现红点，点击标记可跳转。
2. 在 **区间选帧** 中填写起始帧和结束帧，点击 **选中区间** 或 **追加区间**。索引从 `0` 开始，包含两端，例如 `7-15` 共 9 帧。
3. 点击 **选择帧与导出 AI 调试包**，也可直接输入 `7-15,20-22` 选择多个区间。
4. 填写异常现象、期望结果和复现观察，点击 **导出单文件调试包**，将 ZIP 交给 AI 分析。

时间轴蓝色区域表示导出选区。再次点击 **取消异常标记**，会同步移除该帧的异常标记与导出选择。

调试包包含选定帧的原始输入、C 算法输出、源码快照、问题说明、复现脚本，以及运行所需的前置帧历史。标记、选区和说明随实验保存，重新打开实验可继续编辑。

## 数据与实验文件

| 位置 | 内容 |
| --- | --- |
| `datasets/` | 录制数据和测试数据，包含 `frames.npy`、`frame_ids.npy`、`metadata.json` |
| `runs/` | 回放实验、算法输出、源码快照与选帧记录 |
| `build/` | C 编译产物、构建日志与运行日志 |

详细操作、参数通道、数据格式与调试方式见 [使用指南](docs/app-guide.md)。
