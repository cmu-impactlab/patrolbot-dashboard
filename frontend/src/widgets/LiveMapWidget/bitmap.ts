import { decodeRle, type MapData } from "../../types/protocol";

function cssColor(name: string): [number, number, number] {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const hex = raw.replace("#", "");
  const value = parseInt(
    hex.length === 3 ? hex.split("").map((c) => c + c).join("") : hex,
    16,
  );
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

/**
 * Build an offscreen canvas bitmap from an RLE occupancy grid.
 * The bitmap is pre-flipped so image row 0 (top) is the map's highest y —
 * it can then be drawn directly in screen space (y-down) without transforms.
 */
export function buildMapBitmap(map: MapData): HTMLCanvasElement {
  const cells = decodeRle(map.rle);
  const free = cssColor("--map-free");
  const occupied = cssColor("--map-occupied");
  const unknown = cssColor("--map-unknown");

  const canvas = document.createElement("canvas");
  canvas.width = map.width;
  canvas.height = map.height;
  const ctx = canvas.getContext("2d")!;
  const image = ctx.createImageData(map.width, map.height);
  const data = image.data;

  for (let row = 0; row < map.height; row++) {
    const flippedRow = map.height - 1 - row;
    for (let col = 0; col < map.width; col++) {
      const value = cells[row * map.width + col];
      const color = value === 100 ? occupied : value === -1 ? unknown : free;
      const offset = (flippedRow * map.width + col) * 4;
      data[offset] = color[0];
      data[offset + 1] = color[1];
      data[offset + 2] = color[2];
      data[offset + 3] = 255;
    }
  }
  ctx.putImageData(image, 0, 0);
  return canvas;
}
