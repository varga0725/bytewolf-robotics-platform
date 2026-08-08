"""Continuously run one provisioned YOLO model over both dashboard cameras."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import time

from brain.perception.camera_frame import CameraFrame
from brain.perception.detector import BoundingBox, Detection, DetectionResult


def _atomic_json(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document) + "\n", encoding="utf-8")
    temporary.replace(path)


def _detect(model, image_path: Path, source: str, max_age_s: float, confidence: float, allowed: set[str]) -> dict[str, object]:
    from PIL import Image

    captured_at = datetime.fromtimestamp(image_path.stat().st_mtime, UTC)
    with Image.open(image_path) as image:
        width, height = image.size
    result = model(str(image_path), verbose=False)[0]
    names = result.names
    detections = []
    for box in result.boxes:
        x1, y1, x2, y2 = (float(value) for value in box.xyxy[0].tolist())
        label = str(names[int(box.cls[0])])
        score = max(0.0, min(1.0, float(box.conf[0])))
        if score < confidence or label not in allowed:
            continue
        detections.append(Detection(
            label=label,
            confidence=score,
            bbox=BoundingBox(max(0.0, x1), max(0.0, y1), max(1.0, x2-x1), max(1.0, y2-y1)),
        ))
    return DetectionResult(
        captured_at=captured_at, max_age_s=max_age_s, declared_validity="valid",
        frame_width=width, frame_height=height, frame_id=f"{source}:{image_path.stat().st_mtime_ns}",
        detections=tuple(detections), source=source,
    ).to_document()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--period-s", type=float, default=0.2)
    parser.add_argument("--max-age-s", type=float, default=0.75)
    parser.add_argument("--confidence", type=float, default=0.55)
    parser.add_argument(
        "--classes", default="person,car,truck,bus,bicycle,motorcycle,dog,cat",
        help="Comma-separated operational classes; COCO scenery guesses are withheld.",
    )
    parser.add_argument("--camera", action="append", nargs=3, metavar=("NAME", "IMAGE", "OUTPUT"), required=True)
    args = parser.parse_args(argv)
    from ultralytics import YOLO

    model = YOLO(str(args.weights))
    allowed = {item.strip() for item in args.classes.split(",") if item.strip()}
    seen: dict[str, int] = {}
    while True:
        for name, image_text, output_text in args.camera:
            image_path, output_path = Path(image_text), Path(output_text)
            try:
                stamp = image_path.stat().st_mtime_ns
                if seen.get(name) == stamp:
                    continue
                _atomic_json(output_path, _detect(model, image_path, f"yolo11n {name}", args.max_age_s, args.confidence, allowed))
                seen[name] = stamp
            except (FileNotFoundError, OSError) as error:
                print(f"YOLO {name} frame skipped: {error}", flush=True)
        time.sleep(args.period_s)


if __name__ == "__main__":
    raise SystemExit(main())
