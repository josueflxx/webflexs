"""Generate a small original RGBE studio environment for the homepage metal.

Analytic softboxes supply actual high-dynamic-range reflections to the PBR
material. No photographic assets or third-party environment maps are required.
"""
import math
from pathlib import Path


def build_environment():
    width, height = 512, 256
    data = bytearray(f'#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y {height} +X {width}\n'.encode())
    # Longitude, latitude, horizontal/vertical size, linear RGB radiance.
    lights = [
        (-0.9, 0.35, 0.24, 0.8, (7.5, 8.2, 9.5)),
        (1.1, 0.1, 0.14, 1.0, (10.0, 3.2, 0.9)),
        (2.6, 0.4, 0.32, 0.8, (4.8, 6.0, 8.0)),
        (0.1, 1.15, 0.9, 0.2, (6.0, 6.0, 6.0)),
    ]
    for y in range(height):
        latitude = math.pi * (0.5 - (y + 0.5) / height)
        scanline = []
        for x in range(width):
            longitude = 2 * math.pi * ((x + 0.5) / width - 0.5)
            rgb = [0.09, 0.105, 0.13]
            for lon, lat, sx, sy, color in lights:
                dx = (longitude - lon + math.pi) % (2 * math.pi) - math.pi
                dy = latitude - lat
                strength = math.exp(-((dx / sx) ** 6 + (dy / sy) ** 6))
                for c in range(3):
                    rgb[c] += color[c] * strength
            mantissa, exponent = math.frexp(max(rgb))
            scale = mantissa * 256 / max(rgb)
            scanline.append([min(255, int(c * scale)) for c in rgb] + [exponent + 128])
        data.extend((2, 2, width >> 8, width & 255))
        # RGBE scanlines use channel-wise RLE; encode runs and literal packets.
        for channel in range(4):
            values = [pixel[channel] for pixel in scanline]
            i = 0
            while i < width:
                run = 1
                while i + run < width and run < 127 and values[i + run] == values[i]:
                    run += 1
                if run >= 4:
                    data.extend((128 + run, values[i]))
                    i += run
                else:
                    start = i
                    i += 1
                    while i < width and i - start < 128:
                        if i + 3 < width and len(set(values[i:i + 4])) == 1:
                            break
                        i += 1
                    data.append(i - start)
                    data.extend(values[start:i])
    path = Path(__file__).resolve().parents[1] / 'core/static/core/models/clamp-studio.hdr'
    path.write_bytes(data)
    print(f'{path.name}: {len(data):,} bytes')


if __name__ == '__main__':
    build_environment()
