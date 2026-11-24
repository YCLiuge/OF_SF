"""Sample the same four RGB noise textures shipped with the Unity dye panel."""
from functools import lru_cache
import math
import struct
import zlib


@lru_cache(maxsize=4)
def texture(path):
    """Decode the game's noninterlaced 8-bit RGB PNGs without a platform dependency."""
    data = path.read_bytes()
    width, height, depth, color, compression, filtering, interlace = struct.unpack_from(">IIBBBBB", data, 16)
    if (depth, color, compression, filtering, interlace) != (8, 2, 0, 0, 0) or not 0 < width*height <= 65536:
        raise ValueError("Unexpected dye texture format")
    offset = 8
    compressed = bytearray()
    while offset + 12 <= len(data):
        size, kind = struct.unpack_from(">I4s", data, offset)
        if kind == b"IDAT":
            compressed.extend(data[offset + 8:offset + 8 + size])
        offset += size + 12
    pixels = zlib.decompress(compressed)
    stride = width * 3
    if len(pixels) != height * (stride + 1):
        raise ValueError("Invalid dye texture scanlines")
    result = bytearray(height * stride)
    for y in range(height):
        src, dst = y * (stride + 1), y * stride
        mode = pixels[src]
        if mode > 4:
            raise ValueError("Unknown dye scanline filter")
        for x in range(stride):
            index = dst + x
            left = result[index - 3] if x >= 3 else 0
            up = result[index - stride] if y else 0
            if mode == 4:
                corner = result[index - stride - 3] if y and x >= 3 else 0
                p = left + up - corner
                a, b, c = abs(p-left), abs(p-up), abs(p-corner)
                prediction = left if a <= b and a <= c else up if b <= c else corner
            else:
                prediction = (0, left, up, (left + up) // 2)[mode]
            result[index] = (pixels[src + x + 1] + prediction) & 255
    return width, height, bytes(result)


_f32 = struct.Struct("<f")


def single(value):
    return _f32.unpack(_f32.pack(value))[0]


def colors(directory, param, uvy):
    # Source algorithm: of-ps utils/algo.py. Mirror Unity's single-precision UV
    # operations so an index chosen in the original panel addresses the same texel.
    width, height, pixels = texture(directory / f"{param.picture_id}.png")
    exponent = single(single(single(0.2 - 1.5) * uvy) + 1.5)
    transforms = []
    for i in range(0, 64, 4):
        cx, cy, z, w = map(single, param.params[i:i+4])
        if w > 0:
            transforms.append((cx, cy, single(5 * abs(z)), single(w / 2), math.copysign(1, z)))
    result = []
    for i in range(1, 6):
        u, v = single(i / 6), single(uvy)
        for cx, cy, angle, radius, sign in transforms:
            x, y = single(u - cx), single(v - cy)
            dist = single(math.sqrt(single(single(x*x) + single(y*y))))
            if dist >= radius:
                continue
            rot = single(angle * single(math.exp(single(-dist / radius))))
            final = single(rot - single(rot * single(dist / radius))) * sign
            co, si = single(math.cos(final)), single(math.sin(final))
            u = single(single(single(x*co) - single(y*si)) + cx)
            v = single(single(single(x*si) + single(y*co)) + cy)
        px = min(width - 1, int(single(width * single(u - math.floor(u)))))
        py = min(height - 1, int(single(height * single(v - math.floor(v)))))
        index = 3 * ((height - 1 - py) * width + px)
        result.append(tuple(round(single(single(c / 255) ** exponent) * 255)
                            for c in pixels[index:index+3]))
    return result
