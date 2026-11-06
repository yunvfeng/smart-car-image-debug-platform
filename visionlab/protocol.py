"""Incremental decoders based on the firmware's seekfree_assistant structures."""
from dataclasses import dataclass
import math
import struct
import zlib

import numpy as np

WIDTH, HEIGHT = 188, 120
FRAME_BYTES = WIDTH * HEIGHT
VLAB_HEADER = struct.Struct("<4sBBHII")


@dataclass(frozen=True)
class Packet:
    kind: str
    payload: bytes = b""
    values: tuple[float, ...] = ()
    sequence: int | None = None
    flags: int = 0


def parameter_command(channel: int, value: float) -> bytes:
    """The C251 driver reverses float bytes, so wire data is little-endian."""
    if channel not in (1, 2, 3, 4, 6, 7, 8) or not math.isfinite(value):
        raise ValueError("Unsupported tuning channel or non-finite value")
    limits = {1: (0, 32767), 2: (0, 32767), 3: (-3000, 3000),
              4: (0, 32767), 6: (200, 700), 7: (200, 700), 8: (1, 4095)}
    low, high = limits[channel]
    if not low <= value <= high:
        raise ValueError(f"Value must be within {low} .. {high}")
    data = bytearray(struct.pack("<BBBBf", 0x55, 0x20, channel, 0, value))
    data[3] = sum(data) & 255
    return bytes(data)


def encode_vlab(sequence: int, image: bytes, flags: int = 0) -> bytes:
    """V1 image envelope for CRC-equipped future firmware and the local simulator."""
    if len(image) != FRAME_BYTES:
        raise ValueError("Expected one 188x120 grayscale frame")
    header = VLAB_HEADER.pack(b"VLAB", 1, 1, flags, sequence, len(image))
    packet = header + image
    return packet + struct.pack("<I", zlib.crc32(packet))


class StreamDecoder:
    def __init__(self, protocol: str = "seekfree") -> None:
        if protocol not in ("seekfree", "vlab"):
            raise ValueError("Unknown protocol")
        self.protocol = protocol
        self.buffer = bytearray()
        self.errors = 0
        self.discarded_bytes = 0

    def feed(self, data: bytes) -> list[Packet]:
        self.buffer.extend(data)
        packets = []
        while self.buffer:
            packet, consumed = self._vlab() if self.protocol == "vlab" else self._seekfree()
            if not consumed:
                break
            del self.buffer[:consumed]
            if packet:
                packets.append(packet)
        return packets

    def _bad(self) -> tuple[None, int]:
        self.errors += 1
        self.discarded_bytes += 1
        return None, 1

    def _vlab(self) -> tuple[Packet | None, int]:
        if not self.buffer.startswith(b"VLAB"):
            if len(self.buffer) < 4:
                return None, 0
            return self._bad()
        if len(self.buffer) < VLAB_HEADER.size:
            return None, 0
        _, version, kind, flags, sequence, size = VLAB_HEADER.unpack_from(self.buffer)
        if version != 1 or kind != 1 or size != FRAME_BYTES or flags & ~1:
            return self._bad()
        length = VLAB_HEADER.size + size + 4
        if len(self.buffer) < length:
            return None, 0
        expected = struct.unpack_from("<I", self.buffer, length - 4)[0]
        if zlib.crc32(self.buffer[:length - 4]) != expected:
            return self._bad()
        return Packet("image", bytes(self.buffer[VLAB_HEADER.size:length - 4]),
                      sequence=sequence, flags=flags), length

    def _seekfree(self) -> tuple[Packet | None, int]:
        if self.buffer[0] != 0xAA:
            return self._bad()
        if len(self.buffer) < 4:
            return None, 0
        _, function, field, header_length = self.buffer[:4]
        if function == 2:
            if header_length != 8 or field >> 5 != 2:
                return self._bad()
            if len(self.buffer) < 8:
                return None, 0
            width, height = struct.unpack_from("<HH", self.buffer, 4)
            if (width, height) != (WIDTH, HEIGHT):
                return self._bad()
            if field & 0x10:
                return None, 8
            length = 8 + FRAME_BYTES
            if len(self.buffer) < length:
                return None, 0
            return Packet("image", bytes(self.buffer[8:length])), length
        if function == 3:
            if header_length != 8:
                return self._bad()
            if len(self.buffer) < 8:
                return None, 0
            points = struct.unpack_from("<H", self.buffer, 4)[0]
            valid = self.buffer[6] & 7
            count = field & 15
            dimension = field >> 6
            if points > 188 or not points or count != valid.bit_count() or dimension > 2:
                return self._bad()
            length = 8 + points * count * (2 if field & 0x20 else 1) * (2 if dimension == 2 else 1)
            if len(self.buffer) < length:
                return None, 0
            return Packet("boundary", bytes(self.buffer[:length])), length
        if function & 0xF0 == 0x10:
            channels = function & 15
            length = 4 + channels * 4
            if not 1 <= channels <= 8 or header_length != length:
                return self._bad()
            if len(self.buffer) < length:
                return None, 0
            packet = self.buffer[:length]
            if (sum(packet) - packet[2]) & 255 != packet[2]:
                return self._bad()
            values = struct.unpack_from(f"<{channels}f", packet, 4)
            if not all(math.isfinite(value) for value in values):
                return self._bad()
            return Packet("telemetry", values=values), length
        return self._bad()


def boundary_lines(packet: bytes) -> list[np.ndarray]:
    """Decode the current firmware's X-boundaries for display only."""
    field = packet[2]
    if field >> 6 != 0:
        return []
    points = struct.unpack_from("<H", packet, 4)[0]
    dtype = "<u2" if field & 0x20 else "u1"
    values = np.frombuffer(packet, dtype=dtype, offset=8)
    return [line.copy() for line in values.reshape(-1, points)]
