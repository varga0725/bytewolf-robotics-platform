"""Publish canonical VisionSummary artifacts from a local UVC camera.

This is the producer end of the Vision contract: it captures frames from a USB
(UVC) camera -- the Hawkeye in PC-CAM mode, for instance -- optionally runs the
provisioned detector over each frame, and writes the shared
``vision_summary v0.1`` document a consumer (the Cognitive Runtime's
``vision.summary`` plugin) reads. Writes are atomic, so a reader never sees a
half-written artifact.

Observation-only: nothing here can command a flight. Without ``--weights`` it
publishes frames with zero detections, which is the honest way to prove the
capture and artifact path before trusting a model's output.

Example::

    python -m brain.cli.vision_uvc_producer --device-index 0 \
        --artifact var/vision/front-summary.json \
        --weights ~/.cache/bytewolf-vision/yolo11n.pt --frames 10
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import time

from brain.vision.canonical_serialize import vision_summary_to_canonical
from brain.vision.contracts import DetectionResult
from brain.vision.events import canonical_from_detection_result
from brain.vision.uvc import UvcCameraSource, UvcCaptureError

DETECTION_RESULT_V1 = "detection_result.v1"


def _detector(weights: str | None, resolver: object, model_id: str, model_version: str):
    if weights is None:
        return None
    from brain.vision.ultralytics import UltralyticsYoloDetector

    return UltralyticsYoloDetector(model_id, model_version, resolver, weights_path=weights)


def write_artifact(document: dict, destination: Path) -> Path:
    """Write one canonical document atomically."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, destination)
    return destination


def publish_once(
    source: UvcCameraSource,
    detector,
    *,
    artifact: Path,
    max_age_s: float,
    model_id: str,
    model_version: str,
) -> tuple[dict, bytes, DetectionResult]:
    """Capture one frame, detect, and publish the canonical summary.

    Returns the published document plus the frame payload and detection result,
    so a caller can render a preview from exactly what was published.
    """
    frame, payload = source.capture_once()
    produced_at = datetime.now(UTC)
    detections = detector.detect(frame, produced_at) if detector is not None else ()
    result = DetectionResult(
        DETECTION_RESULT_V1, frame, model_id, model_version, produced_at, tuple(detections)
    )
    summary = canonical_from_detection_result(result, ttl=timedelta(seconds=max_age_s))
    document = vision_summary_to_canonical(summary)
    write_artifact(document, artifact)
    return document, payload, result


class _Preview:
    """A live window showing each published frame with its detections drawn.

    It renders the same overlay the dashboard uses, from the exact payload that
    was published, so what you see is what the consumer got. Closing the window
    or pressing q stops the run.
    """

    def __init__(self, title: str) -> None:
        import cv2  # imported lazily: the producer runs headless without --preview

        self._cv2 = cv2
        self._title = title
        self.stopped = False

    def show(self, payload: bytes, result: DetectionResult) -> None:
        from brain.vision.overlay import render_jpeg_overlay

        import numpy as np

        rendered = render_jpeg_overlay(payload, result)
        image = self._cv2.imdecode(np.frombuffer(rendered, dtype=np.uint8), self._cv2.IMREAD_COLOR)
        if image is None:
            return
        self._cv2.imshow(self._title, image)
        if self._cv2.waitKey(1) & 0xFF in (ord("q"), 27):
            self.stopped = True

    def close(self) -> None:
        self._cv2.destroyAllWindows()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--device-index", type=int, default=0, help="UVC device index (macOS AVFoundation order).")
    parser.add_argument("--artifact", required=True, type=Path, help="Where to write the VisionSummary document.")
    parser.add_argument("--weights", default=None, help="Provisioned detector weights; omit to publish zero detections.")
    parser.add_argument("--device-id", default="hawkeye-usb", help="Stable id of the capture device.")
    parser.add_argument("--camera-id", default="front_rgb", help="Camera id; becomes the contract's 'camera:<id>' source.")
    parser.add_argument("--model-id", default="yolo", help="Detector model id recorded in the contract.")
    parser.add_argument("--model-version", default="yolo11n", help="Detector model version recorded in the contract.")
    parser.add_argument("--max-age-s", type=float, default=1.0, help="Freshness budget published as max_age_s.")
    parser.add_argument("--frames", type=int, default=1, help="How many frames to publish (0 = until interrupted).")
    parser.add_argument("--interval-s", type=float, default=0.5, help="Delay between frames.")
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Open a live window showing each published frame with its detections drawn (q or Esc to stop).",
    )
    arguments = parser.parse_args(argv)

    source = UvcCameraSource(
        arguments.device_index,
        device_id=arguments.device_id,
        camera_id=arguments.camera_id,
    )
    try:
        source.open()
    except UvcCaptureError as error:
        print(f"cannot open camera: {error}")
        return 2

    detector = _detector(arguments.weights, source, arguments.model_id, arguments.model_version)
    preview = _Preview(f"ByteWolf Vision — {arguments.camera_id}") if arguments.preview else None
    published = 0
    try:
        while arguments.frames == 0 or published < arguments.frames:
            try:
                document, payload, result = publish_once(
                    source,
                    detector,
                    artifact=arguments.artifact,
                    max_age_s=arguments.max_age_s,
                    model_id=arguments.model_id,
                    model_version=arguments.model_version,
                )
            except UvcCaptureError as error:
                print(f"frame dropped: {error}")
                time.sleep(arguments.interval_s)
                continue
            published += 1
            labels = ", ".join(sorted({d["label"] for d in document["detections"]})) or "none"
            print(
                f"published {published}: {document['frame']['width_px']}x{document['frame']['height_px']} "
                f"detections={document['detection_count']} labels=[{labels}] -> {arguments.artifact}"
            )
            if preview is not None:
                preview.show(payload, result)
                if preview.stopped:
                    print("preview closed")
                    break
            if arguments.frames == 0 or published < arguments.frames:
                time.sleep(arguments.interval_s)
    except KeyboardInterrupt:
        print("stopped")
    finally:
        if preview is not None:
            preview.close()
        source.close()
    return 0 if published else 1


if __name__ == "__main__":
    raise SystemExit(main())
