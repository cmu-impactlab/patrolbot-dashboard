"""Load an occupancy map from a map_server YAML + PGM pair, locally.

Why: the robot's /map is ~7 MB (3192x2205 @ 0.075 m). Streaming it off the
Pi starved /scan and tripped the safety watchdog (see
patrolbot-repo/misc/rviz_vm/RVIZ_VM_README.md), so — like the RViz-on-VM
setup — the dashboard serves a local copy of the static map and the robot
never has to transmit it. The bridge's /map subscription is off by default
for the same reason.

Pure Python, no ROS and no external deps: the YAML subset map_server writes
is trivial, and PGM is a two-line header plus raw bytes.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from ..protocol.messages import MapData, MapOrigin

log = logging.getLogger("static_map")


def _parse_map_yaml(path: Path) -> dict:
    """Parse the flat YAML subset used by nav2 map_server map files."""
    fields: dict = {}
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    origin = [float(part) for part in re.findall(r"-?\d+\.?\d*(?:[eE]-?\d+)?", fields.get("origin", "[0, 0, 0]"))]
    return {
        "image": fields["image"],
        "resolution": float(fields["resolution"]),
        "origin": (origin + [0.0, 0.0, 0.0])[:3],
        "negate": int(fields.get("negate", 0)),
        "occupied_thresh": float(fields.get("occupied_thresh", 0.65)),
        "free_thresh": float(fields.get("free_thresh", 0.196)),
    }


def _read_pgm(path: Path) -> tuple[int, int, bytes]:
    """Read a P5 (binary) PGM; returns (width, height, pixels row-major from top)."""
    data = path.read_bytes()
    # Header: magic, width, height, maxval — whitespace-separated, # comments.
    tokens: list[bytes] = []
    position = 0
    while len(tokens) < 4:
        match = re.compile(rb"\s*(?:#[^\n]*\n\s*)*(\S+)").match(data, position)
        if match is None:
            raise ValueError(f"{path}: truncated PGM header")
        tokens.append(match.group(1))
        position = match.end()
    magic, width, height, maxval = tokens[0], int(tokens[1]), int(tokens[2]), int(tokens[3])
    if magic != b"P5" or maxval != 255:
        raise ValueError(f"{path}: only 8-bit binary (P5) PGM is supported")
    pixels = data[position + 1: position + 1 + width * height]
    if len(pixels) != width * height:
        raise ValueError(f"{path}: expected {width * height} pixels, got {len(pixels)}")
    return width, height, pixels


def load_static_map(yaml_path: str, name: str) -> MapData:
    yaml_file = Path(yaml_path).expanduser()
    meta = _parse_map_yaml(yaml_file)
    width, height, pixels = _read_pgm(yaml_file.parent / meta["image"])

    # map_server convention: occupancy p = pixel/255 (negate) or (255-pixel)/255.
    # p > occupied_thresh -> 100, p < free_thresh -> 0, else -1 (unknown).
    # Build a 256-entry translation table; -1 is carried as byte 255.
    table = bytearray(256)
    for pixel in range(256):
        p = pixel / 255.0 if meta["negate"] else (255 - pixel) / 255.0
        table[pixel] = 100 if p > meta["occupied_thresh"] else (0 if p < meta["free_thresh"] else 255)
    translated = pixels.translate(bytes(table))

    # PGM row 0 is the top of the image; OccupancyGrid row 0 is the origin
    # (bottom) row — flip vertically.
    rows = [translated[row * width:(row + 1) * width] for row in range(height)]
    flipped = b"".join(reversed(rows))

    # RLE over byte runs; regex backreference matching runs at C speed.
    rle: list[tuple[int, int]] = []
    for match in re.finditer(rb"(.)\1*", flipped, re.DOTALL):
        value = match.group(1)[0]
        rle.append((-1 if value == 255 else value, len(match.group(0))))

    log.info("loaded static map %s: %dx%d, %d RLE runs", yaml_file.name, width, height, len(rle))
    return MapData(
        map_version=0,
        name=name,
        resolution=meta["resolution"],
        width=width,
        height=height,
        origin=MapOrigin(x=meta["origin"][0], y=meta["origin"][1], yaw=meta["origin"][2]),
        rle=rle,
    )
