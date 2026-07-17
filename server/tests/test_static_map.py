from app.protocol.messages import decode_rle
from app.telemetry.static_map import load_static_map

YAML = """\
image: tiny.pgm
resolution: 0.075000
origin: [-1, -2.5, 0.000000]
negate: 0
occupied_thresh: 0.65
free_thresh: 0.196
"""


def write_map(tmp_path, pixels: bytes, width: int, height: int):
    (tmp_path / "map.yaml").write_text(YAML)
    header = f"P5\n# a comment\n{width} {height}\n255\n".encode()
    (tmp_path / "tiny.pgm").write_bytes(header + pixels)
    return str(tmp_path / "map.yaml")


def test_thresholds_and_row_flip(tmp_path):
    # 2x2, PGM top row first: top = [black(occupied), white(free)],
    # bottom = [gray(unknown), black(occupied)].
    pixels = bytes([0, 254, 128, 0])
    path = write_map(tmp_path, pixels, 2, 2)
    result = load_static_map(path, "Test")
    cells = decode_rle(result.rle)
    # OccupancyGrid row 0 is the bottom row.
    assert cells == [-1, 100, 100, 0]
    assert (result.width, result.height) == (2, 2)
    assert (result.origin.x, result.origin.y) == (-1.0, -2.5)
    assert result.resolution == 0.075
    assert result.name == "Test"


def test_rle_covers_grid(tmp_path):
    pixels = bytes([254] * 12 + [0] * 4)
    path = write_map(tmp_path, pixels, 4, 4)
    result = load_static_map(path, "Test")
    cells = decode_rle(result.rle)
    assert len(cells) == 16
    # PGM bottom row (black) becomes grid row 0 (occupied).
    assert cells[:4] == [100, 100, 100, 100]
    assert cells[4:] == [0] * 12
