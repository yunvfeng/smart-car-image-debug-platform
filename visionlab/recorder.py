"""Durable session recording. The serial worker records before UI throttling."""
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import zipfile

import numpy as np

from .protocol import FRAME_BYTES, Packet

TELEMETRY_NAMES = ("mid_error_px", "servo_error", "encoder_left", "encoder_right",
                   "gyro_z", "obstacle_lower_row", "motor_pwm_left", "motor_pwm_right")


class SessionRecorder:
    def __init__(self, directory: Path, protocol: str, simulated: bool = False,
                 connection_info: dict | None = None) -> None:
        directory.mkdir(parents=True, exist_ok=False)
        self.directory = directory
        self.count = 0
        self.frame_ids: list[int] = []
        self.missing = 0
        self.previous_sequence: int | None = None
        self.metadata = {
            "schema_version": 1, "status": "recording", "source": "synthetic" if simulated else protocol,
            "protocol": protocol, "started_utc": datetime.now(timezone.utc).isoformat(),
            "width": 188, "height": 120, "initial_state": "process_start" if simulated else "unknown",
            "external_events": "not_replayed", "integrity": "crc32" if protocol == "vlab" else "unverified",
            "frame_id_kind": "processed_id" if protocol == "vlab" else "host_receive_index",
            "mcu_snapshot_verified": False, "parameters_changed": False,
        }
        if connection_info:
            self.metadata["connection"] = dict(connection_info)
        self.raw = (directory / "frames.raw").open("xb")
        self.wire = (directory / "wire.bin").open("xb")
        self.events = (directory / "events.jsonl").open("x", encoding="utf-8")
        self.telemetry_file = (directory / "telemetry.csv").open("x", newline="", encoding="utf-8-sig")
        self.csv = csv.writer(self.telemetry_file)
        self.csv.writerow(("host_monotonic_s", *TELEMETRY_NAMES))
        self._metadata()

    def _metadata(self) -> None:
        temporary = self.directory / "metadata.tmp"
        temporary.write_text(json.dumps(self.metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.directory / "metadata.json")

    def packet(self, packet: Packet, timestamp: float) -> None:
        if packet.kind == "image":
            if len(packet.payload) != FRAME_BYTES:
                raise ValueError("Invalid frame size")
            sequence = packet.sequence if packet.sequence is not None else self.count
            if self.previous_sequence is not None and sequence <= self.previous_sequence:
                raise ValueError("Device frame ID restarted or arrived out of order. Start a new recording")
            if self.previous_sequence is not None:
                self.missing += sequence - self.previous_sequence - 1
            if not self.count and self.metadata["protocol"] == "vlab" and packet.flags & 1 and sequence == 0:
                self.metadata["initial_state"] = "process_start"
            self.raw.write(packet.payload)
            self.raw.flush()
            self.frame_ids.append(sequence)
            self.previous_sequence = sequence
            self.count += 1
        elif packet.kind == "telemetry":
            self.csv.writerow((timestamp, *packet.values))
            self.telemetry_file.flush()
        self.event({"kind": packet.kind, "host_monotonic_s": timestamp,
                    "frame_count": self.count, "device_id": packet.sequence})

    def event(self, event: dict) -> None:
        self.events.write(json.dumps(event, ensure_ascii=False) + "\n")
        self.events.flush()

    def parameter(self, channel: int, value: float, timestamp: float, state: str) -> None:
        self.metadata["parameters_changed"] = True
        self.event({"kind": "parameter", "channel": channel, "value": value,
                    "host_monotonic_s": timestamp, "state": state})

    def close(self, errors: int = 0, failure: str | None = None) -> Path:
        for stream in (self.raw, self.wire, self.events, self.telemetry_file):
            stream.close()
        try:
            if self.count:
                source = np.memmap(self.directory / "frames.raw", dtype=np.uint8, mode="r",
                                   shape=(self.count, 120, 188))
                output = np.lib.format.open_memmap(self.directory / "frames.tmp.npy", mode="w+",
                                                  dtype=np.uint8, shape=source.shape)
                for first in range(0, self.count, 128):
                    output[first:first + 128] = source[first:first + 128]
                output.flush()
                del output, source
                (self.directory / "frames.tmp.npy").replace(self.directory / "frames.npy")
                np.save(self.directory / "frame_ids.npy", np.array(self.frame_ids, dtype=np.uint64))
            self.metadata.update(status="failed" if failure else "complete", frame_count=self.count,
                                 missing_frames=self.missing, decoder_errors=errors, failure=failure)
            self._metadata()
        except (OSError, ValueError) as error:
            self.metadata.update(status="failed", failure=str(error))
            self._metadata()
            raise
        return self.directory


def export_session(source: Path, target: Path) -> None:
    """Lossless archive of the exact session files, with exclusive output creation."""
    metadata = json.loads((source / "metadata.json").read_text(encoding="utf-8"))
    if metadata["status"] == "recording":
        raise ValueError("Stop recording before export")
    files = [p for p in source.iterdir() if p.is_file()]
    if target.resolve().parent == source.resolve():
        raise ValueError("Choose an archive location outside the source session")
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.name)
