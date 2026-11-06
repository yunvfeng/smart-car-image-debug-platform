import json
from pathlib import Path
import shutil
import struct
import time
import unittest
import uuid
import zipfile

import numpy as np

from visionlab.__main__ import DEFAULT_EXE, ROOT, prepare
from visionlab.dataset import load_dataset
from visionlab.live import LiveService
from visionlab.protocol import FRAME_BYTES, Packet, StreamDecoder, boundary_lines, encode_vlab, parameter_command
from visionlab.recorder import SessionRecorder, export_session


class ProtocolTests(unittest.TestCase):
    def test_parameter_wire_matches_firmware(self):
        data = parameter_command(3, -22)
        self.assertEqual(data[:3], b"\x55\x20\x03")
        self.assertEqual(struct.unpack_from("<f", data, 4)[0], -22)
        self.assertEqual((sum(data) - data[3]) & 255, data[3])

    def test_parameter_validation(self):
        for channel, value in ((5, 1), (8, 0), (7, 701), (1, float("nan")), (1, -1)):
            with self.assertRaises(ValueError):
                parameter_command(channel, value)

    def test_split_image_and_embedded_headers(self):
        image = (b"\xaa\x02\x40\x08" * (FRAME_BYTES // 4))
        wire = struct.pack("<BBBBHH", 0xAA, 2, 0x40, 8, 188, 120) + image
        decoder = StreamDecoder()
        packets = []
        for first in range(0, len(wire), 37):
            packets.extend(decoder.feed(wire[first:first + 37]))
        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0].payload, image)
        self.assertEqual(decoder.errors, 0)

    def test_scope_checksum_and_resync(self):
        packet = bytearray(struct.pack("<BBBB2f", 0xAA, 0x12, 0, 12, 1.25, -2))
        packet[2] = sum(packet) & 255
        bad = bytearray(packet)
        bad[-1] ^= 0x01
        decoder = StreamDecoder()
        packets = decoder.feed(bytes(bad) + packet)
        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0].values, (1.25, -2))
        self.assertGreater(decoder.errors, 0)

    def test_boundary_packet(self):
        data = struct.pack("<BBBBHBB", 0xAA, 3, 3, 8, 120, 7, 0) + bytes([30] * 120 + [94] * 120 + [158] * 120)
        packet = StreamDecoder().feed(data)[0]
        lines = boundary_lines(packet.payload)
        self.assertEqual(len(lines), 3)
        self.assertEqual(int(lines[1][80]), 94)

    def test_crc_reject_and_recover(self):
        good = encode_vlab(14, bytes(FRAME_BYTES))
        bad = bytearray(good)
        bad[-5] ^= 1
        decoder = StreamDecoder("vlab")
        packets = decoder.feed(bytes(bad) + good)
        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0].sequence, 14)
        self.assertGreater(decoder.errors, 0)

    def test_wrong_dimensions_rejected(self):
        decoder = StreamDecoder()
        self.assertEqual(decoder.feed(struct.pack("<BBBBHH", 0xAA, 2, 0x40, 8, 320, 240)), [])
        self.assertGreater(decoder.errors, 0)


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / "build/live-tests" / uuid.uuid4().hex
        self.directory.mkdir(parents=True)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        if not self.directory.resolve().is_relative_to((ROOT / "build/live-tests").resolve()):
            raise ValueError("Cleanup escaped test directory")
        shutil.rmtree(self.directory)

    def test_record_export_round_trip(self):
        session = self.directory / "session"
        recorder = SessionRecorder(session, "seekfree", True)
        image = bytes(range(256)) * 88 + bytes(32)
        self.assertEqual(len(image), FRAME_BYTES)
        recorder.packet(Packet("image", image), 1)
        recorder.packet(Packet("image", image), 2)
        recorder.packet(Packet("telemetry", values=(1, 2, 3)), 2)
        recorder.close()
        frames, ids, _ = load_dataset(session)
        self.assertEqual(frames.tobytes(), image * 2)
        self.assertEqual(ids.tolist(), [0, 1])
        export_session(session, self.directory / "export.zip")
        with zipfile.ZipFile(self.directory / "export.zip") as archive:
            self.assertIn("frames.npy", archive.namelist())
            self.assertEqual(archive.read("frames.raw"), image * 2)

    def test_legacy_unknown_history_requires_exploratory_mode(self):
        recorder = SessionRecorder(self.directory / "session", "seekfree")
        recorder.packet(Packet("image", bytes(FRAME_BYTES)), 1)
        recorder.close()
        with self.assertRaises(ValueError):
            load_dataset(recorder.directory)
        frames, ids, metadata = load_dataset(recorder.directory, True)
        self.assertEqual(frames.shape, (1, 120, 188))
        self.assertEqual(metadata["integrity"], "unverified")
        run = prepare(recorder.directory, self.directory / "run", None, False, DEFAULT_EXE, True)
        self.assertTrue(run["exploratory"])

    def test_gap_and_sequence_restart(self):
        recorder = SessionRecorder(self.directory / "session", "vlab")
        recorder.packet(Packet("image", bytes(FRAME_BYTES), sequence=0, flags=1), 1)
        recorder.packet(Packet("image", bytes(FRAME_BYTES), sequence=2), 2)
        with self.assertRaises(ValueError):
            recorder.packet(Packet("image", bytes(FRAME_BYTES), sequence=0), 3)
        recorder.close(failure="sequence restarted")
        metadata = json.loads((recorder.directory / "metadata.json").read_text())
        self.assertEqual(metadata["missing_frames"], 1)
        self.assertEqual(metadata["status"], "failed")

    def test_parameter_changes_preserved(self):
        recorder = SessionRecorder(self.directory / "session", "seekfree", True)
        recorder.packet(Packet("image", bytes(FRAME_BYTES)), 1)
        recorder.parameter(8, 123, 2, "sent")
        recorder.close()
        with self.assertRaisesRegex(ValueError, "parameter"):
            load_dataset(recorder.directory)
        self.assertIn('"channel": 8', (recorder.directory / "events.jsonl").read_text())

    def test_recording_cannot_export(self):
        recorder = SessionRecorder(self.directory / "session", "seekfree")
        try:
            with self.assertRaises(ValueError):
                export_session(recorder.directory, self.directory / "bad.zip")
        finally:
            recorder.close()

    def test_live_worker_records_before_ui(self):
        service = LiveService()
        session = self.directory / "live"
        service.start("本地模拟设备", 115200, "seekfree")
        try:
            service.request("record", str(session))
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if service.read_snapshot()["recorded"] >= 4:
                    break
                time.sleep(.02)
            self.assertGreaterEqual(service.read_snapshot()["recorded"], 4)
            service.request("parameter", 8, 125)
            time.sleep(.1)
        finally:
            service.stop.set()
            service.thread.join(5)
        self.assertFalse(service.thread.is_alive())
        frames, _, metadata = load_dataset(session, True)
        self.assertGreaterEqual(len(frames), 4)
        self.assertEqual(metadata["status"], "complete")
        self.assertTrue(metadata["parameters_changed"])


if __name__ == "__main__":
    unittest.main()
