#!/usr/bin/env python3
"""Run local macOS Vision OCR and emit normalized text observations as JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import Vision
    from Foundation import NSURL
    from Quartz import CGImageSourceCreateImageAtIndex, CGImageSourceCreateWithURL
except ImportError as exc:
    raise SystemExit(
        "Apple Vision bindings are missing. Install pyobjc-framework-Vision and "
        "pyobjc-framework-Quartz in the Python environment."
    ) from exc


def recognize(path: Path) -> list[dict]:
    url = NSURL.fileURLWithPath_(str(path.resolve()))
    source = CGImageSourceCreateWithURL(url, None)
    if source is None:
        raise RuntimeError(f"unable to load image: {path}")
    image = CGImageSourceCreateImageAtIndex(source, 0, None)
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setRecognitionLanguages_(["zh-Hant", "en-US"])
    request.setUsesLanguageCorrection_(False)
    request.setMinimumTextHeight_(0.007)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, {})
    ok, error = handler.performRequests_error_([request], None)
    if not ok:
        raise RuntimeError(f"Vision OCR failed: {error}")
    output = []
    for observation in request.results() or []:
        candidates = observation.topCandidates_(1)
        if not candidates:
            continue
        candidate = candidates[0]
        box = observation.boundingBox()
        output.append(
            {
                "text": str(candidate.string()),
                "confidence": round(float(candidate.confidence()), 4),
                "x": round(float(box.origin.x), 6),
                "y": round(float(box.origin.y), 6),
                "width": round(float(box.size.width), 6),
                "height": round(float(box.size.height), 6),
            }
        )
    return sorted(output, key=lambda item: (-item["y"], item["x"]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows = recognize(args.image)
    payload = json.dumps(rows, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"today-gain OCR error: {exc}", file=sys.stderr)
        raise SystemExit(1)
