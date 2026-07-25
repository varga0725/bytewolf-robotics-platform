"""Record a replayable clip from a local UVC camera.

Live camera runs are unrepeatable: you wave something past the lens once and the
measurement is gone. This records raw frames into the JSONL fixture format
``RecordedJsonlIngest`` replays, so the same input can be fed to different
detectors and trackers and the results actually compared.

Frames are recorded *without* detections by default, which is the point: the
clip is the fixed input, and the model under test is varied at replay time. Pass
``--detections`` to also record what the detector saw while recording, as a
reference of the day rather than as ground truth -- it is not annotation.

Observation-only: nothing here can command a flight.

Example::

    python -m brain.cli.vision_uvc_recorder --device-index 0 \\
        --output var/vision/clips/walk-across.jsonl --seconds 10 --preview
"""

from __future__ import annotations

import argparse
import base64
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import time

from brain.vision.uvc import UvcCameraSource, UvcCaptureError


def frame_record(frame, payload: bytes, detections=()) -> dict:
    """One JSONL record in the format RecordedJsonlIngest accepts."""
    record = {
        "contract_version": frame.contract_version,
        "device_id": frame.device_id,
        "camera_id": frame.camera_id,
        "stream_session_id": frame.stream_session_id,
        "frame_sequence": frame.frame_sequence,
        "captured_at": frame.captured_at.isoformat(),
        "received_at": frame.received_at.isoformat(),
        "calibration_version": frame.calibration_version,
        "payload_hash": frame.payload_hash,
        "encoding": frame.encoding,
        "width_px": frame.width_px,
        "height_px": frame.height_px,
        "latency_ms": frame.latency_ms,
        "dropped_frames": frame.dropped_frames,
        "payload_base64": base64.b64encode(payload).decode("ascii"),
    }
    if detections:
        record["detections"] = [
            {
                "label": detection.label,
                "confidence": detection.confidence,
                "bounding_box": {
                    "x_px": detection.bounding_box.x_px,
                    "y_px": detection.bounding_box.y_px,
                    "width_px": detection.bounding_box.width_px,
                    "height_px": detection.bounding_box.height_px,
                },
                **({"tracker_id": detection.tracker_id} if detection.tracker_id else {}),
            }
            for detection in detections
        ]
    return record


def record_clip(
    source: UvcCameraSource,
    output: Path,
    *,
    frames: int = 0,
    seconds: float = 0.0,
    interval_s: float = 0.0,
    detector=None,
    preview=None,
    now=lambda: datetime.now(UTC),
    sleep=time.sleep,
) -> dict:
    """Capture frames into a JSONL clip; return a summary of what was written."""
    output.parent.mkdir(parents=True, exist_ok=True)
    started = now()
    written = 0
    dropped = 0
    with output.open("w", encoding="utf-8") as handle:
        while True:
            if frames and written >= frames:
                break
            if seconds and (now() - started).total_seconds() >= seconds:
                break
            try:
                frame, payload = source.capture_once()
            except UvcCaptureError:
                dropped += 1
                if dropped > 50 and written == 0:
                    raise
                continue
            detections = detector.detect(frame, now()) if detector is not None else ()
            handle.write(json.dumps(frame_record(frame, payload, detections)) + "\n")
            written += 1
            if preview is not None:
                preview.show(payload, detections, frame)
                if preview.stopped:
                    break
            if interval_s:
                sleep(interval_s)
    return {
        "frames": written,
        "dropped": dropped,
        "seconds": (now() - started).total_seconds(),
        "path": str(output),
        "bytes": output.stat().st_size,
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }


class _RecorderPreview:
    """A live window during recording, so the operator frames the shot."""

    def __init__(self, title: str) -> None:
        import cv2

        self._cv2 = cv2
        self._title = title
        self.stopped = False

    def show(self, payload: bytes, detections, _frame) -> None:
        import numpy as np

        image = self._cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), self._cv2.IMREAD_COLOR)
        if image is None:
            return
        for detection in detections:
            box = detection.bounding_box
            self._cv2.rectangle(
                image, (box.x_px, box.y_px), (box.x_px + box.width_px, box.y_px + box.height_px), (0, 255, 0), 2
            )
        self._cv2.imshow(self._title, image)
        if self._cv2.waitKey(1) & 0xFF in (ord("q"), 27):
            self.stopped = True

    def close(self) -> None:
        self._cv2.destroyAllWindows()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--device-index", type=int, default=0)
    parser.add_argument("--output", required=True, type=Path, help="Where to write the JSONL clip.")
    parser.add_argument("--seconds", type=float, default=0.0, help="Record for this long (0 = use --frames).")
    parser.add_argument("--frames", type=int, default=0, help="Record this many frames (0 = use --seconds).")
    parser.add_argument("--interval-s", type=float, default=0.0, help="Delay between frames; 0 records as fast as the camera allows.")
    parser.add_argument("--device-id", default="hawkeye-usb")
    parser.add_argument("--camera-id", default="front_rgb")
    parser.add_argument("--detections", default=None, metavar="WEIGHTS",
                        help="Also record what this detector saw (a reference of the day, not ground truth).")
    parser.add_argument("--preview", action="store_true", help="Show a live window while recording (q/Esc stops).")
    arguments = parser.parse_args(argv)

    if not arguments.seconds and not arguments.frames:
        arguments.seconds = 10.0

    source = UvcCameraSource(
        arguments.device_index, device_id=arguments.device_id, camera_id=arguments.camera_id
    )
    try:
        source.open()
    except UvcCaptureError as error:
        print(f"cannot open camera: {error}")
        return 2

    detector = None
    if arguments.detections:
        from brain.vision.ultralytics import UltralyticsYoloDetector

        detector = UltralyticsYoloDetector("yolo", "yolo11n", source, weights_path=arguments.detections)

    preview = _RecorderPreview(f"ByteWolf recording — {arguments.camera_id}") if arguments.preview else None
    try:
        summary = record_clip(
            source, arguments.output,
            frames=arguments.frames, seconds=arguments.seconds, interval_s=arguments.interval_s,
            detector=detector, preview=preview,
        )
    except UvcCaptureError as error:
        print(f"recording failed: {error}")
        return 1
    finally:
        if preview is not None:
            preview.close()
        source.close()

    print(
        f"recorded {summary['frames']} frames in {summary['seconds']:.1f}s "
        f"({summary['dropped']} dropped, {summary['bytes'] / 1_000_000:.1f} MB)\n"
        f"  clip:   {summary['path']}\n"
        f"  sha256: {summary['sha256']}"
    )
    return 0 if summary["frames"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
