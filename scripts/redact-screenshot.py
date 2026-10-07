#!/usr/bin/env python3
"""scripts/redact-screenshot.py -- irreversible redaction of regions in a
screenshot before it is used in documentation or an article.

Operator directive 2026-10-04: "a mosaic filter, a pixel change, and then a
top-layer mosaic filter, such that it defeats any attempt to reverse it".
Pixelation on its own is reversible (block averages still encode the text), so
the order here is: DESTROY first, decorate after.

  1. replace the region with cryptographically random noise   (information gone)
  2. mosaic that noise (block average)                         (looks pixelated)
  3. overwrite with FRESH random noise, mosaic again            (top layer)
  4. draw a border + "REDACTED" label so the edit is explicit

Nothing of the original survives under the filter -- there is no "remove the
filter" path back. Metadata (EXIF/text chunks) is stripped on save.

Usage:
  redact-screenshot.py IN.png OUT.png  x0,y0,x1,y1 [x0,y0,x1,y1 ...] [--block 14] [--pad 6]
  redact-screenshot.py IN.png OUT.png  --row y0,y1              # full-width band
Coordinates are pixels; --pad grows every box so edges of glyphs are covered.
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys

from PIL import Image, ImageDraw


def noise(size):
    w, h = size
    return Image.frombytes("RGB", (w, h), secrets.token_bytes(w * h * 3))


def mosaic(img, block):
    w, h = img.size
    small = img.resize((max(1, w // block), max(1, h // block)), Image.BOX)
    return small.resize((w, h), Image.NEAREST)


def redact(img, box, block, pad):
    x0, y0, x1, y1 = box
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(img.width, x1 + pad), min(img.height, y1 + pad)
    size = (x1 - x0, y1 - y0)
    if size[0] <= 0 or size[1] <= 0:
        # 2026-10-04 (duel L4): a box entirely outside the image clamps to a
        # non-positive size and Image.frombytes would crash -- skip it loudly.
        print(f"warning: box {box} is outside the {img.width}x{img.height} image -- skipped", file=sys.stderr)
        return None
    layer = mosaic(noise(size), block)            # 1 + 2: destroy, then pixelate
    layer = mosaic(noise(size), block * 2)        # 3: fresh noise, coarser top mosaic
    img.paste(layer, (x0, y0))
    d = ImageDraw.Draw(img)
    d.rectangle([x0, y0, x1 - 1, y1 - 1], outline=(255, 64, 64), width=3)
    label = "REDACTED"
    if size[0] > 90 and size[1] > 16:
        d.text((x0 + 6, y0 + 3), label, fill=(255, 64, 64))
    return (x0, y0, x1, y1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("dst")
    ap.add_argument("boxes", nargs="*", help="x0,y0,x1,y1 (pixels)")
    ap.add_argument("--row", action="append", default=[], help="y0,y1 full-width band")
    ap.add_argument("--block", type=int, default=14)
    ap.add_argument("--pad", type=int, default=6)
    a = ap.parse_args()
    im = Image.open(a.src).convert("RGB")
    boxes = [tuple(int(v) for v in b.split(",")) for b in a.boxes]
    for r in a.row:
        y0, y1 = (int(v) for v in r.split(","))
        boxes.append((0, y0, im.width, y1))
    if not boxes:
        print("no regions given", file=sys.stderr); return 64
    done = [r for r in (redact(im, b, a.block, a.pad) for b in boxes) if r]
    if not done:
        print("no region inside the image -- nothing written", file=sys.stderr); return 65
    # save without any metadata (new image object, no info dict)
    out = Image.new("RGB", im.size); out.paste(im)
    out.save(a.dst, format="PNG", optimize=True)
    os.chmod(a.dst, 0o644)
    print(f"{a.dst}: {len(done)} region(s) destroyed+mosaicked: {done}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
