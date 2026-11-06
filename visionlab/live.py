"""Serial/TCP ownership, receive/record loop and bounded UI snapshots."""
from collections import deque
import queue
import socket
from pathlib import Path
import struct
import threading
import time

import numpy as np
import serial

from .protocol import Packet, StreamDecoder, encode_vlab, parameter_command
from .recorder import SessionRecorder


class PeerDisconnected(ConnectionError):
    """The TCP client closed its byte stream."""


class TcpConnection:
    """Expose an accepted TCP stream through the recorder's byte I/O interface."""
    in_waiting = 65536

    def __init__(self, connection: socket.socket) -> None:
        self.socket = connection
        connection.settimeout(.1)
        connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if hasattr(socket, "SIO_KEEPALIVE_VALS"):
            connection.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 5000, 1000))

    def read(self, length: int) -> bytes:
        try:
            data = self.socket.recv(length)
        except socket.timeout:
            return b""
        if not data:
            raise PeerDisconnected("小车 TCP 连接已断开")
        return data

    def write(self, data: bytes) -> int:
        return self.socket.send(data)

    def close(self) -> None:
        self.socket.close()


class LiveService:
    def __init__(self) -> None:
        self.commands: queue.Queue = queue.Queue(maxsize=64)
        self.events: queue.Queue = queue.Queue(maxsize=256)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.snapshot = {"image": None, "boundary": [], "telemetry": [], "frames": 0,
                         "bytes": 0, "errors": 0, "recorded": 0, "fps": 0.0}

    def start(self, port: str, baud: int, protocol: str) -> None:
        self._prepare_start()
        self.thread = threading.Thread(target=self._run, args=(port, baud, protocol), daemon=True)
        self.thread.start()

    def start_tcp_server(self, host: str, port: int, protocol: str) -> None:
        if not 1 <= port <= 65535:
            raise ValueError("TCP 端口应为 1 到 65535")
        self._prepare_start()
        self.thread = threading.Thread(target=self._serve, args=(host or "0.0.0.0", port, protocol), daemon=True)
        self.thread.start()

    def _reset_snapshot(self) -> None:
        with self.lock:
            self.snapshot = {"image": None, "boundary": [], "telemetry": [], "frames": 0,
                             "bytes": 0, "errors": 0, "recorded": 0, "fps": 0.0}

    def _prepare_start(self) -> None:
        if self.thread and self.thread.is_alive():
            raise ValueError("Disconnect the current device first")
        self.stop.clear()
        while not self.commands.empty():
            self.commands.get_nowait()
        self._reset_snapshot()

    def _serve(self, host: str, port: int, protocol: str) -> None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind((host, port))
                listener.listen(1)
                listener.settimeout(.2)
                address = f"{host}:{port}"
                self.notify("listening", address)
                while not self.stop.is_set():
                    try:
                        connection, peer = listener.accept()
                    except socket.timeout:
                        continue
                    with connection:
                        if self.stop.is_set():
                            break
                        while not self.commands.empty():
                            self.commands.get_nowait()
                        self._reset_snapshot()
                        self._run(f"TCP {peer[0]}:{peer[1]}", 0, protocol,
                                  TcpConnection(connection))
                    if not self.stop.is_set():
                        self.notify("listening", address)
        except (OSError, ValueError) as error:
            self.notify("error", f"TCP 服务端失败: {error}")
        finally:
            self.notify("disconnected", None)

    def request(self, action: str, *args) -> None:
        if not self.thread or not self.thread.is_alive():
            raise ValueError("Connect a device first")
        self.commands.put_nowait((action, args))

    def notify(self, name: str, value) -> None:
        # Only control/state events use this queue. Image and plot refresh coalesce.
        try:
            self.events.put_nowait((name, value))
        except queue.Full:
            self.stop.set()

    def read_snapshot(self) -> dict:
        with self.lock:
            return dict(self.snapshot)

    def _run(self, port: str, baud: int, protocol: str, tcp_connection: TcpConnection | None = None) -> None:
        recorder = None
        connection = tcp_connection
        failure = None
        decoder = StreamDecoder(protocol)
        simulated = port == "本地模拟设备"
        history = deque(maxlen=240)
        frame_times = deque(maxlen=60)
        frames = byte_count = 0
        demo_sequence = 0
        next_demo = time.monotonic()
        try:
            if not simulated and connection is None:
                connection = serial.serial_for_url(port, baudrate=baud, timeout=.04, write_timeout=.5)
            self.notify("connected", port)
            while not self.stop.is_set():
                now = time.monotonic()
                for _ in range(16):
                    try:
                        action, args = self.commands.get_nowait()
                    except queue.Empty:
                        break
                    if action == "record":
                        if recorder:
                            raise ValueError("A recording is already active")
                        transport = "serial"
                        if tcp_connection:
                            transport = "tcp_server"
                        elif simulated:
                            transport = "simulated"
                        recorder = SessionRecorder(Path(args[0]), protocol, simulated,
                                                   connection_info={"transport": transport, "peer": port})
                        self.notify("recording", str(recorder.directory))
                    elif action == "finish" and recorder:
                        directory = recorder.close(decoder.errors)
                        recorder = None
                        self.notify("saved", str(directory))
                    elif action == "parameter":
                        channel, value = args
                        command = parameter_command(channel, value)
                        if connection:
                            offset = 0
                            while offset < len(command):
                                written = connection.write(command[offset:])
                                if not written:
                                    raise OSError("Connection parameter write made no progress")
                                offset += written
                        state = "模拟设备确认" if simulated else "已发送，设备协议无 ACK"
                        if recorder:
                            recorder.parameter(channel, value, now, state)
                        self.notify("parameter", f"CH{channel} = {value:g} · {state}")
                if simulated:
                    if now < next_demo:
                        self.stop.wait(min(.02, next_demo - now))
                        continue
                    next_demo = now + .05
                    image = self._demo_image(demo_sequence)
                    if protocol == "vlab":
                        data = encode_vlab(demo_sequence, image.tobytes(), int(demo_sequence == 0))
                    else:
                        values = (8 * np.sin(demo_sequence / 12), 12, 205, 207, 5, 0, 2600, 2630)
                        scope = bytearray(struct.pack("<BBBB8f", 0xAA, 0x18, 0, 36, *values))
                        scope[2] = sum(scope) & 255
                        data = struct.pack("<BBBBHH", 0xAA, 2, 0x40, 8, 188, 120) + image.tobytes() + scope
                    demo_sequence += 1
                else:
                    data = connection.read(min(max(connection.in_waiting, 1), 65536))
                byte_count += len(data)
                if recorder:
                    recorder.wire.write(data)
                for packet in decoder.feed(data):
                    if recorder:
                        recorder.packet(packet, now)
                    if packet.kind == "image":
                        frames += 1
                        frame_times.append(now)
                        with self.lock:
                            self.snapshot["image"] = np.frombuffer(packet.payload, np.uint8).reshape(120, 188).copy()
                            self.snapshot["sequence"] = packet.sequence
                            self.snapshot["boundary"] = []
                    elif packet.kind == "boundary":
                        from .protocol import boundary_lines
                        with self.lock:
                            self.snapshot["boundary"] = boundary_lines(packet.payload)
                    elif packet.kind == "telemetry":
                        history.append((now, packet.values))
                fps = (len(frame_times) - 1) / max(.001, frame_times[-1] - frame_times[0]) if len(frame_times) > 1 else 0
                with self.lock:
                    self.snapshot.update(frames=frames, bytes=byte_count, errors=decoder.errors,
                                         recorded=recorder.count if recorder else 0, fps=fps,
                                         telemetry=list(history))
        except PeerDisconnected as error:
            failure = str(error)
            self.notify("peer_closed", failure)
        except (OSError, ValueError, serial.SerialException) as error:
            failure = str(error)
            self.notify("error", failure)
        finally:
            if recorder:
                try:
                    directory = recorder.close(decoder.errors, failure)
                    self.notify("saved", str(directory))
                except (OSError, ValueError) as error:
                    self.notify("error", f"Recording finalization failed: {error}")
            if connection:
                connection.close()
            if tcp_connection:
                self._reset_snapshot()
            self.notify("peer_disconnected" if tcp_connection else "disconnected", failure)

    @staticmethod
    def _demo_image(index: int) -> np.ndarray:
        image = np.full((120, 188), 25, np.uint8)
        center = 94 + int(8 * np.sin(index / 12))
        for row in range(120):
            half = 25 + row // 3
            image[row, center - half:center + half] = 175
        return image
