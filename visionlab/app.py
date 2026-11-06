"""VisionLab desktop app with live acquisition and original-C replay workspaces."""
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import queue
import subprocess
import sys
from .dpi import enable_high_dpi

# This must run before importing Tk, OpenCV or Pillow.
DPI_MODE = enable_high_dpi()

import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
import uuid
import zipfile

import cv2
import numpy as np
from PIL import Image, ImageTk
from serial.tools import list_ports

from .__main__ import DEFAULT_EXE, ROOT, prepare, write_manifest
from .dataset import load_dataset, make_demo, sha256
from .live import LiveService
from .build import BuildError, BUILD_LOG, build_current, build_is_current
from .recorder import TELEMETRY_NAMES, export_session
from .viewer import overlay, overlay_pixels
from .review import load_review, save_review, parse_selection, selection_ranges, format_selection
from .issue_bundle import export_issue_bundle

BG, CARD, FG, MUTED, ACCENT = "#f3f5f9", "#ffffff", "#1e293b", "#64748b", "#2563eb"
BORDER, TINT, VIEW = "#dce3ed", "#eff4ff", "#111827"


class ScrolledPanel(ttk.Frame):
    """Keep tool panels accessible when the window or display height is small."""

    def __init__(self, parent, width=300):
        super().__init__(parent)
        scale = self.winfo_fpixels("1i") / 96
        self.canvas = tk.Canvas(self, background=BG, width=round(width * scale),
                                highlightthickness=0, borderwidth=0, yscrollincrement=round(24 * scale))
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.content = ttk.Frame(self.canvas)
        self.item = self.canvas.create_window(0, 0, window=self.content, anchor="nw")
        self.canvas.bind("<Configure>", self._resize)
        self.content.bind("<Configure>", lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all")))

    def _resize(self, event):
        self.canvas.itemconfigure(self.item, width=event.width)

    def _scroll(self, event):
        bounds = self.canvas.bbox("all")
        if bounds and bounds[3] > self.canvas.winfo_height() and event.delta:
            self.canvas.yview_scroll(-3 if event.delta > 0 else 3, "units")
            return "break"

    def enable_mousewheel(self):
        widgets = [self.canvas]
        while widgets:
            widget = widgets.pop()
            widgets.extend(widget.winfo_children())
            if not isinstance(widget, (ttk.Combobox, ttk.Treeview, tk.Text)):
                widget.bind("<MouseWheel>", self._scroll, add="+")


def unique_path(parent: str, prefix: str) -> Path:
    return ROOT / parent / (prefix + datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:5])


class ImagePanel(tk.Canvas):
    def __init__(self, parent, title: str) -> None:
        super().__init__(parent, bg=VIEW, highlightthickness=0, borderwidth=0,
                         width=420, height=260)
        self.title = title
        self.pixels = None
        self.photo = None
        self.bind("<Configure>", lambda _: self.render())
        self.render()

    def show(self, image: np.ndarray) -> None:
        self.pixels = image
        self.render()

    def clear(self) -> None:
        self.pixels = None
        self.render()

    def render(self) -> None:
        self.delete("all")
        width, height = max(100, self.winfo_width()), max(100, self.winfo_height())
        unit = self.winfo_fpixels("1i") / 96
        margin, top, bottom = 18 * unit, 48 * unit, 30 * unit
        self.create_text(margin, 16 * unit, text=self.title, anchor="nw", fill="#e2e8f0",
                         font=("Microsoft YaHei UI", 10, "bold"))
        self.create_line(margin, top - 8 * unit, width - margin, top - 8 * unit, fill="#263244")
        if self.pixels is None:
            center = (height + top - bottom) / 2
            self.create_rectangle(width / 2 - 22 * unit, center - 50 * unit,
                                  width / 2 + 22 * unit, center - 20 * unit, outline="#475569", width=2)
            self.create_line(width / 2 - 14 * unit, center - 26 * unit, width / 2 - 3 * unit,
                             center - 38 * unit, width / 2 + 14 * unit, center - 26 * unit, fill="#64748b", width=2)
            self.create_text(width / 2, center + 4 * unit, text="等待图像输入", fill="#cbd5e1",
                             font=("Microsoft YaHei UI", 12))
            self.create_text(width / 2, center + 32 * unit, text="接收设备数据或载入实验", fill="#64748b",
                             font=("Microsoft YaHei UI", 9))
            return
        image = Image.fromarray(self.pixels)
        scale = max(.01, min((width - 2 * margin) / image.width, (height - top - bottom) / image.height))
        if scale >= 1:
            scale = int(scale)
        image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                             Image.Resampling.NEAREST)
        self.photo = ImageTk.PhotoImage(image)
        self.create_image(width / 2, (height + top - bottom) / 2, image=self.photo)
        self.create_text(width - margin, height - 10 * unit, anchor="se", fill="#94a3b8",
                         text=f"{self.pixels.shape[1]} × {self.pixels.shape[0]} · {scale:g}× · 最近邻",
                         font=("Microsoft YaHei UI", 9))


class VisionLabApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("VisionLab · 智能车视觉开发工作台")
        scale = root.winfo_fpixels("1i") / 96
        width = min(round(1440 * scale), root.winfo_screenwidth() - 64)
        height = min(round(920 * scale), root.winfo_screenheight() - 96)
        root.geometry(f"{width}x{height}")
        root.minsize(min(round(1180 * scale), width), min(round(760 * scale), height))
        root.configure(bg=BG)
        root.iconbitmap(str(ROOT / "visionlab/assets/visionlab.ico"))
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.live = LiveService()
        self.jobs: queue.Queue = queue.Queue()
        self.busy = False
        self.recording = False
        self.connected = False
        self.listener_active = False
        self.closing = False
        self.last_session: Path | None = None
        self.run_directory: Path | None = None
        self.loaded_dataset: Path | None = None
        self.source_state_checked = 0.0
        self.review = {"marks": {}, "selected": [], "notes": ""}
        self.playback_rate = 12.5
        self.next_frame_at = 0.0
        self.frames = self.ids = None
        self.results: list[dict] = []
        self.baseline: list[dict] = []
        self.index = 0
        self.playing = False
        self.last_live_frame = -1
        self.last_plot = 0.0
        self._style()
        self._layout()
        self.refresh_ports()
        root.after(50, self.poll)
        root.after(50, self.play_tick)

    def _style(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        self.root.option_add("*Font", "{Microsoft YaHei UI} 9")
        self.root.option_add("*TCombobox*Listbox.background", CARD)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", CARD)
        style.configure(".", background=BG, foreground=FG, font=("Microsoft YaHei UI", 9),
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Card.TLabel", background=CARD)
        style.configure("CardMuted.TLabel", background=CARD, foreground=MUTED)
        style.configure("Section.TLabel", background=CARD, font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Title.TLabel", font=("Segoe UI", 21, "bold"), foreground="#0f172a")
        style.configure("Metric.TLabel", foreground=FG, font=("Segoe UI", 22, "bold"), background=CARD)
        style.configure("Badge.TLabel", background=TINT, foreground=ACCENT, padding=(10, 5))
        style.configure("TButton", padding=(12, 7), background=CARD, foreground=FG,
                        borderwidth=1, relief="flat", focusthickness=0)
        style.map("TButton", background=[("disabled", "#f1f3f7"), ("pressed", "#e2e8f0"), ("active", "#eef2f7")],
                  foreground=[("disabled", "#94a3b8")], bordercolor=[("focus", ACCENT)])
        style.configure("Accent.TButton", background=ACCENT, foreground=CARD, bordercolor=ACCENT)
        style.map("Accent.TButton", background=[("disabled", "#cbd5e1"), ("pressed", "#1e40af"), ("active", "#1d4ed8")],
                  foreground=[("disabled", "#f8fafc"), ("!disabled", CARD)],
                  bordercolor=[("!disabled", ACCENT)])
        style.configure("Small.TButton", padding=(9, 4))
        style.configure("TEntry", fieldbackground=CARD, foreground=FG, insertcolor=FG,
                        padding=(8, 6), borderwidth=1)
        style.map("TEntry", bordercolor=[("focus", ACCENT)], fieldbackground=[("disabled", BG)])
        style.configure("TCombobox", fieldbackground=CARD, background=CARD, foreground=FG,
                        arrowcolor=MUTED, padding=(8, 6), borderwidth=1)
        style.map("TCombobox", fieldbackground=[("readonly", CARD), ("disabled", BG)],
                  foreground=[("disabled", "#94a3b8"), ("readonly", FG)], bordercolor=[("focus", ACCENT)])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(24, 11), background=BG, foreground=MUTED,
                        font=("Microsoft YaHei UI", 10, "bold"))
        style.map("TNotebook.Tab", background=[("selected", CARD), ("active", "#e9eef7")],
                  foreground=[("selected", ACCENT)])
        style.configure("TLabelframe", background=CARD, bordercolor=BORDER, borderwidth=1, relief="solid")
        style.configure("TLabelframe.Label", background=CARD, foreground=FG,
                        font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TCheckbutton", background=CARD, foreground=FG, focuscolor=CARD)
        style.map("TCheckbutton", background=[("active", CARD)])
        style.configure("Horizontal.TScale", background=CARD, troughcolor="#e2e8f0",
                        borderwidth=0, sliderlength=18)
        style.configure("TScrollbar", background="#cbd5e1", troughcolor=BG, borderwidth=0,
                        arrowsize=12, relief="flat")
        style.map("TScrollbar", background=[("active", "#94a3b8")])
        style.configure("TSeparator", background=BORDER)
        style.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=FG,
                        borderwidth=0, rowheight=round(34 * self.root.winfo_fpixels("1i") / 96))
        style.configure("Treeview.Heading", background="#edf1f7", foreground=MUTED,
                        relief="flat", font=("Microsoft YaHei UI", 9, "bold"), padding=(10, 8))
        style.map("Treeview", background=[("selected", "#dbeafe")], foreground=[("selected", "#1e40af")])
        style.map("Treeview.Heading", background=[("active", "#e2e8f0")])

    def _layout(self) -> None:
        header = ttk.Frame(self.root, padding=(24, 14))
        header.pack(fill="x")
        ttk.Label(header, text="VisionLab", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="智能车视觉开发工作台", style="Muted.TLabel").pack(side="left", padx=18)
        ttk.Button(header, text="使用说明", command=lambda: os.startfile(ROOT / "README.md")).pack(side="right")
        ttk.Label(header, text="STC32  /  MT9V03X", style="Badge.TLabel").pack(side="right", padx=16)
        self.status = tk.StringVar(value="就绪 · 可作为 TCP 服务端等待小车连接，或选择串口与本地模拟设备")
        status_bar = ttk.Frame(self.root, padding=(24, 9))
        status_bar.pack(side="bottom", fill="x")
        ttk.Label(status_bar, text="●", foreground="#16a34a").pack(side="left", padx=(0, 8))
        status_label = ttk.Label(status_bar, textvariable=self.status, style="Muted.TLabel")
        status_label.pack(side="left", fill="x", expand=True)
        status_bar.bind("<Configure>", lambda event: status_label.configure(wraplength=max(100, event.width - 80)))
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=24)
        self.live_tab = ttk.Frame(self.notebook, padding=(0, 16, 0, 0))
        self.replay_tab = ttk.Frame(self.notebook, padding=(0, 16, 0, 0))
        self.notebook.add(self.live_tab, text="实车工作台")
        self.notebook.add(self.replay_tab, text="图像实验室")
        self._live_layout()
        self._replay_layout()

    def _live_layout(self) -> None:
        connection_card = ttk.Frame(self.live_tab, style="Card.TFrame", padding=14)
        connection_card.pack(fill="x", pady=(0, 12))
        connection_header = ttk.Frame(connection_card, style="Card.TFrame")
        connection_header.pack(fill="x", pady=(0, 10))
        ttk.Label(connection_header, text="设备连接", style="Section.TLabel").pack(side="left")
        self.connection_state = tk.StringVar(value="● 未监听")
        ttk.Label(connection_header, textvariable=self.connection_state, style="Badge.TLabel").pack(side="right")
        connection = ttk.Frame(connection_card, style="Card.TFrame")
        connection.pack(fill="x")
        self.transport = tk.StringVar(value="WiFi / TCP 服务端")
        self.transport_box = ttk.Combobox(connection, textvariable=self.transport, state="readonly", width=21,
                                          values=("WiFi / TCP 服务端", "串口 / USB CDC"))
        self.transport_box.pack(side="left", padx=(0, 8))
        self.transport_box.bind("<<ComboboxSelected>>", self.transport_changed)
        self.serial_options = ttk.Frame(connection, style="Card.TFrame")
        self.port = tk.StringVar(value="本地模拟设备")
        self.ports = ttk.Combobox(self.serial_options, textvariable=self.port, width=20)
        self.ports.pack(side="left")
        ttk.Button(self.serial_options, text="刷新端口", command=self.refresh_ports).pack(side="left", padx=6)
        self.baud = tk.StringVar(value="115200")
        self.baud_box = ttk.Combobox(self.serial_options, textvariable=self.baud, values=(115200, 460800, 921600, 1500000), width=10)
        self.baud_box.pack(side="left")
        self.tcp_options = ttk.Frame(connection, style="Card.TFrame")
        self.tcp_options.pack(side="left")
        self.listen_host = tk.StringVar(value="0.0.0.0")
        self.listen_port = tk.StringVar(value="8086")
        ttk.Label(self.tcp_options, text="监听 IP", style="CardMuted.TLabel").pack(side="left", padx=(0, 4))
        self.listen_host_entry = ttk.Entry(self.tcp_options, textvariable=self.listen_host, width=14)
        self.listen_host_entry.pack(side="left")
        ttk.Label(self.tcp_options, text="端口", style="CardMuted.TLabel").pack(side="left", padx=4)
        self.listen_port_entry = ttk.Entry(self.tcp_options, textvariable=self.listen_port, width=6)
        self.listen_port_entry.pack(side="left")
        self.protocol = tk.StringVar(value="逐飞助手（当前固件）")
        self.protocol_box = ttk.Combobox(connection, textvariable=self.protocol, state="readonly", width=22,
                                        values=("逐飞助手（当前固件）", "VisionLab v1（CRC32）"))
        self.protocol_box.pack(side="left", padx=8)
        self.connect_button = ttk.Button(connection, text="开始监听", style="Accent.TButton", command=self.connect)
        self.connect_button.pack(side="left")
        metrics = ttk.Frame(self.live_tab)
        metrics.pack(fill="x")
        self.metrics = []
        for title in ("接收帧数", "图像 FPS", "接收 MiB", "解析异常"):
            card = ttk.Frame(metrics, padding=(16, 10), style="Card.TFrame")
            card.pack(side="left", fill="x", expand=True, padx=(0, 8) if title != "解析异常" else 0)
            ttk.Label(card, text=title, background=CARD, foreground=MUTED).pack(anchor="w")
            variable = tk.StringVar(value="0")
            ttk.Label(card, textvariable=variable, style="Metric.TLabel", background=CARD).pack(anchor="w")
            self.metrics.append(variable)
        body = ttk.Frame(self.live_tab)
        body.pack(fill="both", expand=True, pady=12)
        controls_scroll = ScrolledPanel(body, width=292)
        controls_scroll.pack(side="left", fill="y", padx=(0, 16))
        controls = controls_scroll.content
        parameters = ttk.Frame(controls, style="Card.TFrame", padding=14)
        parameters.pack(fill="x")
        ttk.Label(parameters, text="在线调参", style="Section.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(parameters, text="Q10 / 原始单位", style="CardMuted.TLabel").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(4, 10))
        parameters.columnconfigure(1, weight=1)
        self.parameter_values = {}
        self.parameter_buttons = []
        rows = [(1, "舵机 KP", 2000), (2, "舵机 KD", 498), (3, "陀螺仪 KG", 22),
                (4, "电机 KP", 654), (6, "最低速度", 200), (7, "最高速度", 280), (8, "曝光时间", 120)]
        for row, (channel, title, value) in enumerate(rows):
            ttk.Label(parameters, text=title, style="Card.TLabel").grid(row=row + 2, column=0, sticky="w", pady=4)
            variable = tk.StringVar(value=str(value))
            self.parameter_values[channel] = variable
            ttk.Entry(parameters, textvariable=variable, width=8).grid(row=row + 2, column=1, padx=8, sticky="ew")
            button = ttk.Button(parameters, text="发送", style="Small.TButton",
                                command=lambda c=channel: self.send_parameter(c), state="disabled")
            button.grid(row=row + 2, column=2)
            self.parameter_buttons.append(button)
        ttk.Label(controls, text="数值为本地输入，连接后按需发送。\n逐飞协议不提供参数 ACK。",
                  style="Muted.TLabel", wraplength=260).pack(anchor="w", pady=10)
        recording = ttk.Frame(controls, style="Card.TFrame", padding=14)
        recording.pack(fill="x")
        ttk.Label(recording, text="录制与数据", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        self.record_button = ttk.Button(recording, text="开始录制", style="Accent.TButton", command=self.toggle_record, state="disabled")
        self.record_button.pack(fill="x", pady=3)
        ttk.Button(recording, text="导出上次会话 ZIP", command=self.export_live).pack(fill="x", pady=3)
        ttk.Button(recording, text="送往图像实验室 →", command=self.send_to_replay).pack(fill="x", pady=3)
        ttk.Button(recording, text="打开数据目录", command=lambda: self.open_folder(ROOT / "datasets")).pack(fill="x", pady=3)
        self.record_state = tk.StringVar(value="尚未录制")
        ttk.Label(controls, textvariable=self.record_state, wraplength=260, style="Muted.TLabel").pack(anchor="w", pady=10)
        controls_scroll.enable_mousewheel()
        display = ttk.Frame(body)
        display.pack(side="left", fill="both", expand=True)
        display.columnconfigure(0, weight=1)
        display.rowconfigure(0, weight=4, minsize=100)
        display.rowconfigure(2, weight=1, minsize=60)
        display.rowconfigure(4, weight=1, minsize=36)
        self.live_image = ImagePanel(display, "实时图像  /  灰度输入与边线叠加")
        self.live_image.grid(row=0, column=0, sticky="nsew")
        self.integrity = tk.StringVar(value="逐飞图像无 CRC / 无处理帧号 · 实车数据标记为未验证")
        integrity_label = ttk.Label(display, textvariable=self.integrity, style="Muted.TLabel")
        integrity_label.grid(row=1, column=0, sticky="w", pady=6)
        self.plot = tk.Canvas(display, bg=CARD, height=110, highlightthickness=0)
        self.plot.grid(row=2, column=0, sticky="nsew")
        self.telemetry_text = tk.StringVar(value="CH1 中线误差  ·  CH2 舵机误差  ·  CH3/4 编码器  ·  CH5 陀螺仪  ·  CH7/8 PWM")
        telemetry_label = ttk.Label(display, textvariable=self.telemetry_text, style="Muted.TLabel")
        telemetry_label.grid(row=3, column=0, sticky="w", pady=6)
        def resize_display(event):
            for label in (integrity_label, telemetry_label):
                label.configure(wraplength=max(100, event.width - 12))
        display.bind("<Configure>", resize_display)
        self.log = tk.Text(display, height=3, width=1, bg=CARD, fg=MUTED, relief="flat", padx=12, pady=8,
                           font=("Consolas", 9), state="disabled")
        self.log.grid(row=4, column=0, sticky="nsew")

    def _replay_layout(self) -> None:
        inputs = ttk.Frame(self.replay_tab, style="Card.TFrame", padding=14)
        inputs.pack(fill="x", pady=(0, 12))
        inputs.columnconfigure(1, weight=1)
        ttk.Label(inputs, text="数据集", style="CardMuted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 16))
        self.dataset_path = tk.StringVar(value=str(ROOT / "datasets/demo"))
        ttk.Entry(inputs, textvariable=self.dataset_path, width=20).grid(row=0, column=1, sticky="ew")
        ttk.Button(inputs, text="选择数据集", command=self.browse_dataset).grid(row=0, column=2, padx=8)
        ttk.Button(inputs, text="生成测试数据", command=self.create_demo).grid(row=0, column=3, sticky="ew")
        ttk.Label(inputs, text="视觉源码", style="CardMuted.TLabel").grid(row=1, column=0, sticky="w", pady=8)
        self.source_file = tk.StringVar(value=str(ROOT / "smart-car/project/code/image.c"))
        ttk.Entry(inputs, textvariable=self.source_file, width=20).grid(row=1, column=1, sticky="ew")
        ttk.Button(inputs, text="选择 C 文件…", command=self.browse_source).grid(row=1, column=2, padx=8)
        source_tools = ttk.Frame(inputs, style="Card.TFrame")
        source_tools.grid(row=1, column=3, sticky="ew")
        ttk.Button(source_tools, text="定位", command=self.locate_source).pack(side="left")
        ttk.Button(source_tools, text="编辑源码", command=self.edit_source).pack(side="left", padx=(6, 0))
        actions = ttk.Frame(inputs, style="Card.TFrame")
        actions.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(4, 0))
        self.replay_button = ttk.Button(actions, text="构建并回放", style="Accent.TButton", command=self.run_replay)
        self.replay_button.pack(side="left")
        ttk.Button(actions, text="重新构建 C", command=self.build).pack(side="left", padx=6)
        ttk.Button(actions, text="编译输出", command=self.show_build_log).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="打开已有实验", command=self.browse_run).pack(side="left")
        ttk.Button(actions, text="导出实验 ZIP", command=self.export_run).pack(side="left", padx=6)
        self.source_state = tk.StringVar(value="正在检查源码版本…")
        source_label = ttk.Label(inputs, textvariable=self.source_state, style="CardMuted.TLabel")
        source_label.grid(row=3, column=0, columnspan=4, sticky="w", pady=(8, 0))
        inputs.bind("<Configure>", lambda event: source_label.configure(wraplength=max(100, event.width - 32)))

        body = ttk.Frame(self.replay_tab)
        body.pack(fill="both", expand=True)
        sidebar = ScrolledPanel(body, width=286)
        sidebar.pack(side="right", fill="y", padx=(14, 0))
        controls = sidebar.content
        playback = ttk.Frame(controls, style="Card.TFrame", padding=14)
        playback.pack(fill="x")
        playback.columnconfigure((0, 1, 2), weight=1)
        ttk.Label(playback, text="回放控制", style="Section.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))
        ttk.Button(playback, text="◀ 上一帧", style="Small.TButton", command=lambda: self.step(-1)).grid(row=1, column=0, sticky="ew")
        self.play_button = ttk.Button(playback, text="播放", style="Accent.TButton", command=self.toggle_play)
        self.play_button.grid(row=1, column=1, sticky="ew", padx=5)
        ttk.Button(playback, text="下一帧 ▶", style="Small.TButton", command=lambda: self.step(1)).grid(row=1, column=2, sticky="ew")
        fields = ttk.Frame(playback, style="Card.TFrame")
        fields.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        fields.columnconfigure((0, 1), weight=1)
        ttk.Label(fields, text="播放速率 / FPS", style="CardMuted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(fields, text="帧索引", style="CardMuted.TLabel").grid(row=0, column=1, sticky="w", padx=(10, 0))
        self.rate_text = tk.StringVar(value="12.5")
        rate = ttk.Combobox(fields, textvariable=self.rate_text, width=7,
                            values=(1, 2, 5, 10, 12.5, 20, 30, 60, 120))
        rate.grid(row=1, column=0, sticky="ew", pady=5)
        rate.bind("<<ComboboxSelected>>", self.set_playback_rate)
        rate.bind("<Return>", self.set_playback_rate)
        rate.bind("<FocusOut>", self.set_playback_rate)
        self.jump_index = tk.StringVar(value="0")
        jump = ttk.Frame(fields, style="Card.TFrame")
        jump.grid(row=1, column=1, sticky="ew", padx=(10, 0))
        jump.columnconfigure(0, weight=1)
        jump_entry = ttk.Entry(jump, textvariable=self.jump_index, width=5)
        jump_entry.grid(row=0, column=0, sticky="ew")
        jump_entry.bind("<Return>", lambda _: self.jump())
        ttk.Button(jump, text="跳转", style="Small.TButton", command=self.jump).grid(row=0, column=1, padx=(4, 0))
        self.mark_button = ttk.Button(playback, text="标记异常帧", command=self.mark_frame)
        self.mark_button.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(6, 0))

        interval = ttk.Frame(controls, style="Card.TFrame", padding=14)
        interval.pack(fill="x", pady=12)
        interval.columnconfigure((0, 1), weight=1)
        ttk.Label(interval, text="区间选帧", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(interval, text="索引从 0 开始，包含起止帧", style="CardMuted.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(4, 10))
        self.range_start = tk.StringVar(value="0")
        self.range_end = tk.StringVar(value="0")
        ttk.Label(interval, text="起始帧", style="CardMuted.TLabel").grid(row=2, column=0, sticky="w")
        ttk.Label(interval, text="结束帧", style="CardMuted.TLabel").grid(row=2, column=1, sticky="w", padx=(8, 0))
        ttk.Entry(interval, textvariable=self.range_start, width=7).grid(row=3, column=0, sticky="ew", pady=5)
        ttk.Entry(interval, textvariable=self.range_end, width=7).grid(row=3, column=1, sticky="ew", padx=(8, 0), pady=5)
        ttk.Button(interval, text="当前帧设起点", style="Small.TButton", command=lambda: self.range_start.set(str(self.index))).grid(
            row=4, column=0, sticky="ew")
        ttk.Button(interval, text="当前帧设终点", style="Small.TButton", command=lambda: self.range_end.set(str(self.index))).grid(
            row=4, column=1, sticky="ew", padx=(8, 0))
        ttk.Button(interval, text="选中区间", command=self.select_range).grid(row=5, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(interval, text="追加区间", command=lambda: self.select_range(append=True)).grid(
            row=5, column=1, sticky="ew", padx=(8, 0), pady=(10, 0))

        tools = ttk.Frame(controls, style="Card.TFrame", padding=14)
        tools.pack(fill="x")
        ttk.Label(tools, text="问题定位与导出", style="Section.TLabel").pack(anchor="w", pady=(0, 10))
        ttk.Button(tools, text="选择帧与导出 AI 调试包", style="Accent.TButton", command=self.open_issue_dialog).pack(fill="x")
        ttk.Button(tools, text="导出当前 PNG", command=self.export_image).pack(fill="x", pady=6)
        ttk.Button(tools, text="在 VS Code 调试此帧", command=self.debug_frame).pack(fill="x")
        options = ttk.Frame(controls, style="Card.TFrame", padding=14)
        options.pack(fill="x", pady=(12, 0))
        ttk.Label(options, text="运行设置", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        self.target = tk.BooleanVar(value=False)
        self.exploratory = tk.BooleanVar(value=False)
        ttk.Checkbutton(options, text="启用靶点识别", variable=self.target).pack(anchor="w", pady=3)
        ttk.Checkbutton(options, text="允许片段探索", variable=self.exploratory).pack(anchor="w", pady=3)
        ttk.Label(options, text="片段探索从初始状态重算，\n结果记录为探索运行。", style="CardMuted.TLabel").pack(anchor="w", pady=(6, 0))
        sidebar.enable_mousewheel()

        workspace = ttk.Frame(body)
        workspace.pack(side="left", fill="both", expand=True)
        timeline_card = ttk.Frame(workspace, style="Card.TFrame", padding=(14, 10))
        timeline_card.pack(side="bottom", fill="x", pady=(10, 0))
        self.frame_info = tk.StringVar(value="选择数据集并运行，或打开已有实验")
        frame_label = ttk.Label(timeline_card, textvariable=self.frame_info, style="Card.TLabel")
        frame_label.pack(anchor="w")
        self.timeline = tk.DoubleVar(value=0)
        self.markers = tk.Canvas(timeline_card, height=24, bg=CARD, highlightthickness=0)
        self.markers.pack(fill="x", pady=(4, 0))
        self.markers.bind("<Configure>", lambda _: self.draw_markers())
        self.markers.bind("<Button-1>", self.marker_click)
        self.slider = ttk.Scale(timeline_card, from_=0, to=1, variable=self.timeline, command=self.seek)
        self.slider.pack(fill="x", pady=(0, 6))
        legend = ttk.Frame(timeline_card, style="Card.TFrame")
        legend.pack(fill="x")
        ttk.Label(legend, text="● 异常标记", foreground="#e11d48", style="Card.TLabel").pack(side="left")
        ttk.Label(legend, text="■ 导出选区", foreground=ACCENT, style="Card.TLabel").pack(side="left", padx=14)
        ttk.Label(legend, text="点击标记跳转", style="CardMuted.TLabel").pack(side="right")
        timeline_card.bind("<Configure>", lambda event: frame_label.configure(wraplength=max(100, event.width - 32)))
        self.replay_info = tk.StringVar(value="完整历史按序执行 · 原始 C 源码 · 每次运行重置进程状态")
        replay_label = ttk.Label(workspace, textvariable=self.replay_info, style="Muted.TLabel")
        replay_label.pack(anchor="w", pady=(0, 8))
        workspace.bind("<Configure>", lambda event: replay_label.configure(wraplength=max(100, event.width - 12)))
        images = ttk.Frame(workspace)
        images.pack(fill="both", expand=True)
        images.columnconfigure((0, 1), weight=1, uniform="images")
        images.rowconfigure(0, weight=1)
        self.raw_image = ImagePanel(images, "01  原始输入")
        self.raw_image.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.result_image = ImagePanel(images, "02  C 算法输出")
        self.result_image.grid(row=0, column=1, sticky="nsew")
        ttk.Label(images, text="188 × 120  /  uint8", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(6, 0))
        output_legend = ttk.Label(images, text="青/黄 控制线 · 绿 中线 · 紫 参考列", style="Muted.TLabel")
        output_legend.grid(row=1, column=1, sticky="w", pady=(6, 0))
        images.bind("<Configure>", lambda event: output_legend.configure(wraplength=max(100, event.width // 2 - 16)))

    def report(self, text: str) -> None:
        self.status.set(text)
        self.log.configure(state="normal")
        self.log.insert("end", datetime.now().strftime("%H:%M:%S  ") + text + "\n")
        if int(self.log.index("end-1c").split(".")[0]) > 100:
            self.log.delete("1.0", "20.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def error(self, error: Exception | str) -> None:
        logging.error("%s", error)
        self.report(str(error))
        messagebox.showerror("VisionLab", str(error), parent=self.root)

    def refresh_ports(self) -> None:
        self.ports.configure(values=["本地模拟设备", *[p.device for p in list_ports.comports()]])

    def transport_changed(self, _event=None) -> None:
        tcp = self.transport.get() == "WiFi / TCP 服务端"
        self.serial_options.pack_forget()
        self.tcp_options.pack_forget()
        options = self.tcp_options if tcp else self.serial_options
        options.pack(side="left", before=self.protocol_box)
        self.connect_button.configure(text="开始监听" if tcp else "连接设备")
        self.connection_state.set("● 未监听" if tcp else "● 未连接")

    def connection_inputs(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.ports.configure(state=state)
        self.baud_box.configure(state=state)
        self.listen_host_entry.configure(state=state)
        self.listen_port_entry.configure(state=state)
        self.transport_box.configure(state="readonly" if enabled else "disabled")
        self.protocol_box.configure(state="readonly" if enabled else "disabled")

    def connect(self) -> None:
        if self.connected or self.listener_active:
            self.live.stop.set()
            self.connect_button.configure(state="disabled")
            self.report("正在停止连接并保存录制数据…")
            return
        try:
            protocol = "seekfree" if self.protocol.get().startswith("逐飞") else "vlab"
            self.integrity.set("VisionLab v1 · CRC32 校验图像流，MCU 快照所有权仍需实测" if protocol == "vlab"
                               else "逐飞图像无 CRC / 无处理帧号 · 实车数据标记为未验证")
            if self.transport.get() == "WiFi / TCP 服务端":
                self.live.start_tcp_server(self.listen_host.get().strip(), int(self.listen_port.get()), protocol)
            else:
                baud = int(self.baud.get())
                if baud <= 0:
                    raise ValueError("波特率必须大于零")
                self.live.start(self.port.get(), baud, protocol)
            self.connect_button.configure(state="disabled")
            self.connection_inputs(False)
        except (ValueError, OSError) as error:
            self.error(error)

    def send_parameter(self, channel: int) -> None:
        try:
            from .protocol import parameter_command
            value = float(self.parameter_values[channel].get())
            parameter_command(channel, value)
            if self.live.read_snapshot().get("frames", 0) == 0:
                raise ValueError("尚未收到有效帧，请先确认设备与协议")
            self.live.request("parameter", channel, value)
        except (ValueError, queue.Full) as error:
            self.error(error)

    def toggle_record(self) -> None:
        try:
            if self.recording:
                self.live.request("finish")
            else:
                path = unique_path("datasets", "capture_")
                self.live.request("record", str(path))
            self.record_button.configure(state="disabled")
        except (ValueError, queue.Full) as error:
            self.error(error)

    def export_live(self) -> None:
        if not self.last_session:
            self.error("请先完成一次录制")
            return
        target = filedialog.asksaveasfilename(defaultextension=".zip", initialfile=self.last_session.name + ".zip")
        if target:
            session = self.last_session
            self.background("会话导出", lambda: export_session(session, Path(target)))

    def send_to_replay(self) -> None:
        if not self.last_session or not (self.last_session / "frames.npy").exists():
            self.error("请先停止并保存含有图像的录制")
            return
        self.dataset_path.set(str(self.last_session))
        self.notebook.select(self.replay_tab)
        self.report("已送往图像实验室；实车片段可能需要显式勾选“允许片段探索”")

    @staticmethod
    def open_folder(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    def browse_dataset(self) -> None:
        value = filedialog.askdirectory(initialdir=ROOT / "datasets")
        if value:
            self.dataset_path.set(value)

    def create_demo(self) -> None:
        path = unique_path("datasets", "synthetic_")
        try:
            make_demo(path, 600)
            self.dataset_path.set(str(path))
            self.report("已生成 600 帧合成数据，可用于软件流程验证")
        except (ValueError, OSError) as error:
            self.error(error)

    def background(self, title: str, action) -> None:
        if self.busy:
            self.error("请等待当前后台任务完成")
            return
        self.busy = True
        self.replay_button.configure(state="disabled")
        self.report(title + "…")
        def worker():
            try:
                self.jobs.put(("done", title, action()))
            except Exception as error:
                logging.exception("Background job failed")
                self.jobs.put(("error", title, error))
        threading.Thread(target=worker, daemon=True).start()

    def build(self) -> None:
        source = Path(self.source_file.get())
        self.background("重新构建所选 C 算法", lambda: build_current(force=True, image_source=source))

    def browse_source(self) -> None:
        value = filedialog.askopenfilename(title="选择视觉 C 模块文件（image.c / ring.c / laser.c 等）",
                                          initialdir=Path(self.source_file.get()).parent,
                                          filetypes=[("C 算法源码", "*.c"), ("所有文件", "*.*")])
        if value:
            self.source_file.set(value)
            self.source_state_checked = 0
            self.report("已选择外部视觉源码，同目录配套视觉模块将优先参与构建")

    def locate_source(self) -> None:
        path = Path(self.source_file.get()).resolve()
        if not path.is_file():
            self.error("所选源码文件不存在")
            return
        subprocess.Popen(["explorer.exe", "/select,", str(path)])

    def show_build_log(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("VisionLab · C 编译输出")
        window.geometry("1000x600")
        text = ScrolledText(window, bg=BG, fg=FG, insertbackground=FG, font=("Consolas", 11), wrap="none")
        text.pack(fill="both", expand=True)
        content = BUILD_LOG.read_text(encoding="utf-8", errors="replace") if BUILD_LOG.exists() else "尚无编译输出。保存 C 源码后点击“构建并回放”。"
        text.insert("1.0", content)
        text.configure(state="disabled")

    def edit_source(self) -> None:
        import shutil
        executable = shutil.which("code")
        path = Path(self.source_file.get()).resolve()
        if executable:
            subprocess.Popen([executable, str(ROOT), "--goto", str(path)],
                             creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            os.startfile(path)
        self.report(f"正在编辑 {path.name} · 保存后点击“构建并回放”即可使用新代码")

    def run_replay(self) -> None:
        dataset = Path(self.dataset_path.get())
        target, exploratory = self.target.get(), self.exploratory.get()
        source = Path(self.source_file.get()).resolve()
        self.playing = False
        def action():
            build_current(image_source=source)
            run = unique_path("runs", "experiment_")
            manifest = prepare(dataset, run, None, target, DEFAULT_EXE, exploratory)
            result = subprocess.run(manifest["command"], capture_output=True, cwd=ROOT,
                                    creationflags=subprocess.CREATE_NO_WINDOW, timeout=180)
            manifest.update(status="complete" if result.returncode == 0 else "failed", exit_code=result.returncode)
            if result.returncode == 0:
                manifest["results_sha256"] = sha256(run / "results.jsonl")
            write_manifest(run, manifest)
            (run / "stderr.log").write_bytes(result.stderr)
            if result.returncode:
                raise RuntimeError(result.stderr.decode(errors="replace"))
            return run
        self.background("检查源码、构建并顺序回放", action)

    def browse_run(self) -> None:
        value = filedialog.askdirectory(initialdir=ROOT / "runs")
        if value:
            try:
                self.load_run(Path(value))
            except (ValueError, OSError, KeyError) as error:
                self.error(error)

    def load_run(self, directory: Path) -> None:
        manifest = json.loads((directory / "run.json").read_text(encoding="utf-8"))
        if manifest.get("status") != "complete":
            raise ValueError("请选择状态为 complete 的完整实验")
        dataset = Path(manifest["dataset"])
        if sha256(dataset / "frames.npy") != manifest["frames_sha256"] or sha256(dataset / "frame_ids.npy") != manifest["frame_ids_sha256"]:
            raise ValueError("实验对应的数据集已变化")
        frames, ids, _ = load_dataset(dataset, manifest.get("exploratory", False))
        results = [json.loads(line) for line in (directory / "results.jsonl").read_text().splitlines()]
        if len(results) != manifest["through_index"] + 1 or any(r["index"] != i for i, r in enumerate(results)):
            raise ValueError("实验结果缺少连续帧")
        same_dataset = self.loaded_dataset == dataset.resolve()
        review = load_review(directory, len(results))
        self.baseline = self.results if same_dataset else []
        self.frames, self.ids, self.results = frames, ids, results
        self.run_directory = directory
        self.review = review
        selected = review["selected"]
        self.range_start.set(str(min(selected)) if selected else "0")
        self.range_end.set(str(max(selected)) if selected else "0")
        if manifest.get("algorithm_entry"):
            self.source_file.set(manifest["algorithm_entry"])
        self.loaded_dataset = dataset.resolve()
        self.dataset_path.set(str(dataset))
        self.slider.configure(to=len(results) - 1)
        mode = "片段探索 · 初始状态重算" if manifest.get("exploratory") else "完整历史顺序回放"
        self.replay_info.set(f"{mode}  ·  {len(results)} 帧  ·  {directory.name}  ·  视觉避障关闭")
        self.show_frame(0)
        self.draw_markers()

    def show_frame(self, index: int) -> None:
        if not self.results:
            return
        self.index = max(0, min(len(self.results) - 1, index))
        self.timeline.set(self.index)
        self.jump_index.set(str(self.index))
        result = self.results[self.index]
        self.raw_image.show(self.frames[self.index])
        self.result_image.show(cv2.cvtColor(overlay_pixels(self.frames[self.index], result), cv2.COLOR_BGR2RGB))
        text = (f"索引 {self.index} / {len(self.results) - 1}    帧号 {int(self.ids[self.index])}    "
                f"参考列 {result['reference_col']}    中线[80] {result['mid'][80]}    "
                f"环岛状态 {result['ring_step']}    左/右环 {result['ring_l']}/{result['ring_r']}")
        if self.index < len(self.baseline):
            count = sum(a != b for a, b in zip(result["mid"], self.baseline[self.index]["mid"]))
            text += f"    与上次实验相比，中线变化 {count} 行"
        self.frame_info.set(text)
        self.mark_button.configure(text="取消异常标记" if str(self.index) in self.review["marks"] else "标记异常帧")
        self.draw_markers()

    def seek(self, value: str) -> None:
        index = int(float(value))
        if index != self.index:
            self.show_frame(index)

    def step(self, delta: int) -> None:
        self.playing = False
        self.play_button.configure(text="播放")
        self.show_frame(self.index + delta)

    def jump(self) -> None:
        try:
            index = int(self.jump_index.get())
            if not 0 <= index < len(self.results):
                raise ValueError("索引超出当前实验范围")
            self.show_frame(index)
        except ValueError as error:
            self.error(error)

    def toggle_play(self) -> None:
        if self.results:
            if not self.set_playback_rate():
                return
            self.playing = not self.playing
            self.next_frame_at = time.monotonic()
            self.play_button.configure(text="暂停" if self.playing else "播放")

    def play_tick(self) -> None:
        now = time.monotonic()
        if self.playing and now >= self.next_frame_at:
            self.show_frame(self.index + 1)
            self.next_frame_at = max(self.next_frame_at + 1 / self.playback_rate, time.monotonic())
            if self.index == len(self.results) - 1:
                self.playing = False
                self.play_button.configure(text="播放")
        delay = max(1, min(50, round((self.next_frame_at - time.monotonic()) * 1000))) if self.playing else 50
        self.root.after(delay, self.play_tick)

    def set_playback_rate(self, _event=None) -> bool:
        try:
            value = float(self.rate_text.get())
            if not .1 <= value <= 240:
                raise ValueError("播放速率应为 0.1 到 240 FPS")
            self.playback_rate = value
            self.next_frame_at = time.monotonic()
            return True
        except ValueError:
            self.rate_text.set(f"{self.playback_rate:g}")
            self.report("播放速率应为 0.1 到 240 FPS")
            return False

    def mark_frame(self) -> None:
        if self.run_directory:
            key = str(self.index)
            if key in self.review["marks"]:
                del self.review["marks"][key]
                self.review["selected"] = [index for index in self.review["selected"] if index != self.index]
            else:
                self.review["marks"][key] = {"frame_id": int(self.ids[self.index]), "note": ""}
                self.review["selected"] = sorted(set(self.review["selected"]) | {self.index})
            save_review(self.run_directory, self.review)
            self.show_frame(self.index)

    def draw_markers(self) -> None:
        self.markers.delete("all")
        if not self.results:
            return
        span = max(1, len(self.results) - 1)
        width = max(32, self.markers.winfo_width())
        for first, last in selection_ranges(self.review["selected"]):
            start = 8 + first * (width - 16) / span
            end = 8 + last * (width - 16) / span
            if first == last:
                self.markers.create_rectangle(start - 5, 2, end + 5, 21, outline=ACCENT, width=2)
            else:
                self.markers.create_rectangle(start, 2, end, 21, fill="#dbeafe", outline=ACCENT, width=2)
        for key in self.review["marks"]:
            x = 8 + int(key) * (width - 16) / span
            self.markers.create_line(x, 10, x, 24, fill="#e11d48", width=2)
            self.markers.create_oval(x - 4, 4, x + 4, 12, fill="#e11d48", outline="")

    def marker_click(self, event) -> None:
        candidates = set(map(int, self.review["marks"])) | set(self.review["selected"])
        if not candidates or not self.results:
            return
        span = max(1, len(self.results) - 1)
        width = max(32, self.markers.winfo_width())
        index = min(candidates, key=lambda i: abs(8 + i * (width - 16) / span - event.x))
        if abs(8 + index * (width - 16) / span - event.x) <= 10:
            self.playing = False
            self.play_button.configure(text="播放")
            self.show_frame(index)

    def select_range(self, append: bool = False) -> None:
        if not self.run_directory or not self.results:
            self.error("请先运行或打开一个实验")
            return
        try:
            text = f"{self.range_start.get().strip()}-{self.range_end.get().strip()}"
            indices = parse_selection(text, len(self.results))
            selected = sorted(set(self.review["selected"]) | set(indices)) if append else indices
            review = dict(self.review, selected=selected)
            save_review(self.run_directory, review)
            self.review = review
            self.draw_markers()
            self.report(f"已选择区间 {text}，当前共选中 {len(selected)} 帧")
        except (ValueError, OSError) as error:
            self.error(error)

    def open_issue_dialog(self) -> None:
        if self.busy:
            self.error("请等待当前后台任务完成后再导出调试包")
            return
        if not self.run_directory:
            self.error("请先运行或打开一个实验")
            return
        self.playing = False
        self.play_button.configure(text="播放")
        run = self.run_directory
        window = tk.Toplevel(self.root)
        window.transient(self.root)
        window.grab_set()
        window.title("VisionLab · AI 调试包")
        window.configure(background=BG)
        unit = window.winfo_fpixels("1i") / 96
        width = min(round(1020 * unit), window.winfo_screenwidth() - 80)
        height = min(round(820 * unit), window.winfo_screenheight() - 100)
        window.geometry(f"{width}x{height}")
        window.minsize(min(round(720 * unit), width), min(round(560 * unit), height))
        body = ttk.Frame(window, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="AI 调试包", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w")
        ttk.Label(body, text="选定关键帧，记录问题，打包完整复现材料。", style="Muted.TLabel").pack(anchor="w", pady=(4, 14))
        footer = ttk.Frame(body, style="Card.TFrame", padding=14)
        footer.pack(side="bottom", fill="x")
        form_scroll = ScrolledPanel(body, width=980)
        form_scroll.pack(fill="both", expand=True, pady=(0, 12))
        form = form_scroll.content
        selection_card = ttk.Frame(form, style="Card.TFrame", padding=14)
        selection_card.pack(fill="x", padx=(0, 8))
        ttk.Label(selection_card, text="01  选择帧", style="Section.TLabel").pack(anchor="w")
        ttk.Label(selection_card, text="输入索引或区间，例如 12,15,20-24。索引从 0 开始，包含区间两端。",
                  style="CardMuted.TLabel", wraplength=600).pack(anchor="w", pady=(6, 0))
        selection = tk.StringVar(value=format_selection(self.review["selected"] or [self.index]))
        selection_entry = ttk.Entry(selection_card, textvariable=selection)
        selection_entry.pack(fill="x", pady=8)
        buttons = ttk.Frame(selection_card, style="Card.TFrame")
        buttons.pack(fill="x")
        selection_summary = tk.StringVar()
        summary_label = ttk.Label(selection_card, textvariable=selection_summary, style="Card.TLabel", foreground=ACCENT)
        summary_label.pack(anchor="w", pady=(10, 0))
        selection_card.bind("<Configure>", lambda event: summary_label.configure(wraplength=max(100, event.width - 32)))
        table = ttk.Frame(selection_card, style="Card.TFrame")
        table.pack(fill="x", pady=10)
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        marks = ttk.Treeview(table, columns=("index", "count", "id", "state"),
                             show="headings", height=3, selectmode="extended")
        marks.heading("index", text="异常 / 选定帧索引")
        marks.heading("count", text="帧数")
        marks.heading("id", text="设备 / 接收帧号（首尾）")
        marks.heading("state", text="选择与异常标记")
        marks.column("index", width=round(220 * unit), minwidth=round(170 * unit))
        marks.column("count", width=round(70 * unit), minwidth=round(60 * unit), anchor="center")
        marks.column("id", width=round(250 * unit), minwidth=round(200 * unit))
        marks.column("state", width=round(180 * unit), minwidth=round(160 * unit))
        marks.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=marks.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=marks.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        marks.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        ttk.Label(selection_card, text="Ctrl / Shift 多选区间 · 双击跳转到起始帧", style="CardMuted.TLabel").pack(anchor="w")
        table_state = {"selected": None, "rows": {}}

        def refresh_table(indices):
            picked = set(indices)
            anomalies = set(map(int, self.review["marks"]))
            rows = [(first, last, True) for first, last in selection_ranges(indices)]
            rows += [(first, last, False) for first, last in selection_ranges(list(anomalies - picked))]
            table_state["selected"] = indices
            table_state["rows"] = {}
            for item in marks.get_children():
                marks.delete(item)
            selected_rows = []
            for first, last, chosen in sorted(rows):
                key = f"range_{first}_{last}"
                table_state["rows"][key] = (first, last)
                index_text = format_selection([first]) if first == last else f"{first}-{last}"
                first_id, last_id = int(self.ids[first]), int(self.ids[last])
                id_text = str(first_id) if first == last else f"{first_id} … {last_id}"
                anomaly_count = sum(first <= index <= last for index in anomalies)
                state_text = f"已选 · 异常 {anomaly_count} 帧" if chosen else "异常标记 · 待选"
                marks.insert("", "end", iid=key,
                             values=(index_text, last - first + 1, id_text, state_text))
                if chosen:
                    selected_rows.append(key)
            marks.selection_set(selected_rows)

        def preview_selection(*_args):
            try:
                indices = parse_selection(selection.get(), len(self.results))
                if indices != table_state["selected"]:
                    refresh_table(indices)
                selection_summary.set(f"当前选择 {len(indices)} 帧，共 {len(selection_ranges(indices))} 个区间，包含两端；点击应用选择保存")
            except ValueError as error:
                refresh_table([])
                selection_summary.set(str(error))

        def apply_selection():
            try:
                indices = parse_selection(selection.get(), len(self.results))
                review = dict(self.review, selected=indices)
                save_review(run, review)
                self.review = review
                selection.set(format_selection(indices))
                self.draw_markers()
                selection_summary.set(f"已保存 {len(indices)} 帧，共 {len(selection_ranges(indices))} 个区间，包含两端")
                return True
            except (ValueError, OSError) as error:
                self.error(error)
                return False
        def add_current():
            try:
                indices = parse_selection(selection.get(), len(self.results))
                selection.set(format_selection(sorted(set(indices) | {self.index})))
                apply_selection()
            except ValueError as error:
                self.error(error)
        ttk.Button(buttons, text="加入当前帧", command=add_current).pack(side="left")
        def select_all_marked():
            selection.set(format_selection(list(map(int, self.review["marks"]))))
            apply_selection()
        ttk.Button(buttons, text="选择全部异常标记", command=select_all_marked).pack(side="left", padx=6)
        ttk.Button(buttons, text="应用选择", style="Accent.TButton", command=apply_selection).pack(side="left")
        selection_entry.bind("<Return>", lambda _: apply_selection())
        def select_marked(_event):
            indices = set()
            for key in marks.selection():
                first, last = table_state["rows"][key]
                indices.update(range(first, last + 1))
            indices = sorted(indices)
            # Ignore selection events queued by programmatic table refreshes.
            if indices == table_state["selected"]:
                return
            # Keep existing rows and the selection anchor during Ctrl/Shift selection.
            table_state["selected"] = indices
            selection.set(format_selection(indices))
            apply_selection()
        def jump_to_range(_event):
            if marks.selection():
                first, _ = table_state["rows"][marks.selection()[0]]
                self.show_frame(first)
        marks.bind("<<TreeviewSelect>>", select_marked)
        marks.bind("<Double-1>", jump_to_range)
        selection.trace_add("write", preview_selection)
        preview_selection()
        notes_card = ttk.Frame(form, style="Card.TFrame", padding=14)
        notes_card.pack(fill="both", expand=True, pady=(12, 0), padx=(0, 8))
        ttk.Label(notes_card, text="02  描述问题", style="Section.TLabel").pack(anchor="w")
        ttk.Label(notes_card, text="记录异常现象、期望行为与复现观察，帮助 AI 定位代码。",
                  style="CardMuted.TLabel", wraplength=600).pack(anchor="w", pady=(6, 2))
        notes = ScrolledText(notes_card, height=5, width=30, bg="#f8fafc", fg=FG, insertbackground=ACCENT,
                             font=("Microsoft YaHei UI", 10), wrap="word", relief="flat", borderwidth=0,
                             highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT, padx=12, pady=10)
        notes.insert("1.0", self.review.get("notes", ""))
        notes.pack(fill="both", expand=True, pady=8)
        bundle_hint = ttk.Label(footer, text="原始输入  /  C 输出  /  源码快照  /  问题说明  /  前置历史 → 单个 ZIP 文件",
                                style="CardMuted.TLabel")
        bundle_hint.pack(anchor="w")
        def save_notes():
            if apply_selection():
                self.review["notes"] = notes.get("1.0", "end-1c")
                save_review(run, self.review)
                return True
            return False
        def export():
            if not save_notes():
                return
            if not self.review["selected"]:
                self.error("请至少选择一帧")
                return
            path = filedialog.asksaveasfilename(parent=window, defaultextension=".zip", initialfile="visionlab_issue.zip")
            if path:
                review = json.loads(json.dumps(self.review))
                self.background("AI 调试包导出到 " + path, lambda: export_issue_bundle(
                    run, Path(path), review["selected"], review["notes"], review))
        bottom = ttk.Frame(footer, style="Card.TFrame")
        bottom.pack(fill="x", pady=(10, 0))
        ttk.Button(bottom, text="保存说明", command=save_notes).pack(side="left")
        ttk.Button(bottom, text="导出单文件调试包", style="Accent.TButton", command=export).pack(side="right")
        export_status = ttk.Label(footer, textvariable=self.status, style="CardMuted.TLabel")
        export_status.pack(anchor="w", pady=(8, 0))
        def resize_footer(event):
            for label in (bundle_hint, export_status):
                label.configure(wraplength=max(100, event.width - 32))
        footer.bind("<Configure>", resize_footer)
        form_scroll.enable_mousewheel()
        window.protocol("WM_DELETE_WINDOW", lambda: window.destroy() if save_notes() else None)

    def export_image(self) -> None:
        if not self.results:
            return
        path = filedialog.asksaveasfilename(defaultextension=".png", initialfile=f"frame_{self.index}.png")
        if path:
            try:
                display = cv2.cvtColor(overlay(self.frames[self.index], self.results[self.index], int(self.ids[self.index])), cv2.COLOR_BGR2RGB)
                with Path(path).open("xb") as stream:
                    Image.fromarray(display).save(stream, format="PNG")
                self.report("已导出当前显示层 PNG")
            except OSError as error:
                self.error(error)

    def export_run(self) -> None:
        if not self.run_directory:
            return
        run = self.run_directory
        path = filedialog.asksaveasfilename(defaultextension=".zip", initialfile=run.name + ".zip")
        if path:
            if Path(path).resolve().is_relative_to(run.resolve()):
                self.error("请把 ZIP 保存到实验目录之外")
                return
            def action():
                with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
                    for file in run.rglob("*"):
                        if file.is_file():
                            archive.write(file, file.relative_to(run).as_posix())
            self.background("实验导出", action)

    def debug_frame(self) -> None:
        if not self.run_directory:
            return
        import shutil
        executable = shutil.which("code")
        if not executable:
            self.error("VS Code 的 code 命令尚未加入 PATH，请在 VS Code 中打开本工作区")
            return
        manifest = json.loads((self.run_directory / "run.json").read_text(encoding="utf-8"))
        source = Path(manifest.get("algorithm_entry", ROOT / "smart-car/project/code/image.c"))
        if not source.is_file():
            self.error("实验对应的源文件已移动，可从 AI 调试包中的源码快照分析")
            return
        lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
        line = next((i for i, text in enumerate(lines, 1) if "void Search_line(" in text), 1)
        subprocess.Popen([executable, str(ROOT), "--goto", str(source) + f":{line}"],
                         creationflags=subprocess.CREATE_NO_WINDOW)
        self.report(f"VS Code 已打开。条件 visionlab_frame_index == {self.index}，F5 输入数据集、索引、算法入口文件及回放模式")

    def draw_plot(self, history: list) -> None:
        self.plot.delete("all")
        width, height = max(100, self.plot.winfo_width()), max(60, self.plot.winfo_height())
        chart_height = height - 40
        for fraction in (.25, .5, .75):
            y = 28 + chart_height * fraction
            self.plot.create_line(12, y, width - 12, y, fill="#e8edf4")
        colors = ("#2563eb", "#b45309", "#059669", "#9333ea")
        for channel, color in enumerate(colors):
            values = [sample[1][channel] for sample in history if len(sample[1]) > channel]
            if len(values) < 2:
                continue
            low, high = min(values), max(values)
            span = max(high - low, 1)
            coords = []
            for i, value in enumerate(values):
                coords.extend((12 + i * (width - 24) / (len(values) - 1), height - 12 - (value - low) * chart_height / span))
            self.plot.create_line(*coords, fill=color, width=1.5)
            self.plot.create_text(12 + channel * (width - 24) / 4, 8, text=f"CH{channel + 1}  {values[-1]:.1f}",
                                  fill=color, anchor="nw", font=("Consolas", 9))
        if history:
            self.telemetry_text.set("  ·  ".join(f"{name}={value:.1f}" for name, value in zip(TELEMETRY_NAMES, history[-1][1])))

    def poll(self) -> None:
        for _ in range(100):
            try:
                kind, value = self.live.events.get_nowait()
            except queue.Empty:
                break
            if kind == "listening":
                self.listener_active = True
                self.connected = False
                self.connection_inputs(False)
                self.connect_button.configure(text="停止监听", state="normal")
                self.connection_state.set("● 等待小车连接 · " + value)
                self.record_button.configure(state="disabled", text="开始录制")
                for button in self.parameter_buttons:
                    button.configure(state="disabled")
                self.report("TCP 服务端正在监听 " + value)
            elif kind == "connected":
                self.connected = True
                self.last_live_frame = -1
                self.live_image.clear()
                self.connect_button.configure(text="停止监听" if self.listener_active else "断开连接", state="normal")
                self.record_button.configure(state="normal")
                for button in self.parameter_buttons:
                    button.configure(state="normal")
                self.connection_state.set("● " + value)
                self.report("已连接 " + value)
            elif kind == "peer_disconnected":
                self.connected = self.recording = False
                self.last_live_frame = -1
                self.live_image.clear()
                self.connection_state.set("● 等待小车重新连接")
                self.record_button.configure(state="disabled", text="开始录制")
                for button in self.parameter_buttons:
                    button.configure(state="disabled")
            elif kind == "disconnected":
                self.connected = self.recording = False
                self.listener_active = False
                tcp = self.transport.get() == "WiFi / TCP 服务端"
                self.connect_button.configure(text="开始监听" if tcp else "连接设备", state="normal")
                self.connection_inputs(True)
                self.record_button.configure(state="disabled", text="开始录制")
                for button in self.parameter_buttons:
                    button.configure(state="disabled")
                self.connection_state.set("● 已断开")
            elif kind == "recording":
                self.recording = True
                self.record_button.configure(state="normal", text="停止并保存")
                self.record_state.set("正在录制 · " + Path(value).name)
            elif kind == "saved":
                self.recording = False
                self.last_session = Path(value)
                self.record_button.configure(state="normal" if self.connected else "disabled", text="开始录制")
                self.record_state.set("已保存 · " + self.last_session.name)
                self.report("会话已保存 " + value)
            elif kind == "error":
                self.error(value)
            else:
                self.report(value)
        snapshot = self.live.read_snapshot()
        for variable, value in zip(self.metrics, (snapshot["frames"], f'{snapshot["fps"]:.1f}',
                                                f'{snapshot["bytes"] / 1048576:.2f}', snapshot["errors"])):
            variable.set(str(value))
        if snapshot["image"] is not None and snapshot["frames"] != self.last_live_frame:
            self.last_live_frame = snapshot["frames"]
            display = cv2.cvtColor(snapshot["image"], cv2.COLOR_GRAY2RGB)
            for line, color in zip(snapshot["boundary"], ((40, 220, 240), (50, 240, 120), (250, 210, 30))):
                for row, col in enumerate(line):
                    if row < 120 and col < 188:
                        display[row, int(col)] = color
            self.live_image.show(display)
        if time.monotonic() - self.last_plot > .25:
            self.draw_plot(snapshot["telemetry"])
            self.last_plot = time.monotonic()
        if not self.busy and time.monotonic() - self.source_state_checked > 1.5:
            current = build_is_current(image_source=Path(self.source_file.get()))
            self.source_state.set("源码已构建 · 可直接回放" if current else "源码已修改或尚未构建 · 下次回放自动编译")
            self.source_state_checked = time.monotonic()
        for _ in range(10):
            try:
                kind, title, value = self.jobs.get_nowait()
            except queue.Empty:
                break
            self.busy = False
            self.replay_button.configure(state="normal")
            if kind == "error":
                if isinstance(value, BuildError):
                    self.report(str(value))
                    self.show_build_log()
                else:
                    self.error(value)
            else:
                if isinstance(value, Path):
                    try:
                        self.load_run(value)
                    except (OSError, ValueError, KeyError) as error:
                        self.error(error)
                self.report(title + "完成")
        if self.closing and not self.busy and not (self.live.thread and self.live.thread.is_alive()):
            self.root.destroy()
            return
        self.root.after(50, self.poll)

    def close(self) -> None:
        self.closing = True
        self.playing = False
        self.live.stop.set()
        self.report("正在关闭，等待后台任务与录制落盘…")


def main() -> None:
    (ROOT / "build").mkdir(exist_ok=True)
    logging.basicConfig(filename=ROOT / "build/app.log", encoding="utf-8", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.info("DPI awareness mode: %s", DPI_MODE)
    root = tk.Tk()
    root.report_callback_exception = lambda _type, error, _trace: messagebox.showerror("VisionLab", str(error))
    VisionLabApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
