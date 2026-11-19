"""Scaled transport regression: keep original Lua unchanged on scaled XWayland."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'companion'))
import hermeswow  # pyright: ignore[reportMissingImports]


def frame(payload):
    header = bytes([0xc7, 0x1a, 0, 7]) + len(payload).to_bytes(2, 'big')
    data = header + payload
    s1 = s2 = 0
    for value in data[2:]:
        s1 = (s1 + value) % 255
        s2 = (s2 + s1) % 255
    return data + bytes([s1, s2])


def raster(payload, pitch):
    data = frame(payload)
    bits = ''.join(f'{byte:08b}' for byte in data)
    bits += '0' * (-len(bits) % 3)
    cells = [int(bits[i:i + 3], 2) for i in range(0, len(bits), 3)]
    width, height = 1600, 384
    buf = bytearray(width * height * 4)
    for i, value in enumerate(cells):
        x0, x1 = int((i % 200) * pitch), int((i % 200 + 1) * pitch)
        y0, y1 = int((i // 200) * pitch), int((i // 200 + 1) * pitch)
        pixel = bytes([255 if value & 1 else 0, 255 if value & 2 else 0,
                       255 if value & 4 else 0, 255])
        for y in range(y0, y1):
            for x in range(x0, x1):
                buf[(y * width + x) * 4:(y * width + x + 1) * 4] = pixel
    return bytes(buf), width, height


class ScaledStripTests(unittest.TestCase):
    def test_decoder_handles_scaled_xwayland_without_changing_lua(self):
        self.assertTrue(callable(getattr(hermeswow, 'decode_scaled_strip', None)),
                        'scaled XWayland needs a checksum-validated decoder')
        payload = b'HW1\x1etesttoken\x1f7\x1fhello'
        for pitch in [4, 16/3, 5, 6, 3.2, 8]:
            with self.subTest(pitch=pitch):
                self.assertEqual(hermeswow.decode_scaled_strip(*raster(payload, pitch)), (7, payload))

    def test_maximum_payload_is_not_truncated_by_scaled_crop(self):
        self.assertTrue(callable(getattr(hermeswow, 'decode_scaled_strip', None)), 'missing scaled decoder')
        payload = bytes(i % 251 for i in range(3200))
        self.assertEqual(hermeswow.decode_scaled_strip(*raster(payload, 16/3)), (7, payload))

    def test_random_scene_and_corrupted_checksum_are_not_requests(self):
        self.assertTrue(callable(getattr(hermeswow, 'decode_scaled_strip', None)), 'missing scaled decoder')
        blank = bytes(1600 * 384 * 4)
        self.assertIsNone(hermeswow.decode_scaled_strip(blank, 1600, 384))
        buf, w, h = raster(b'hello', 16/3)
        data = bytearray(buf)
        # Destroy the magic header across all plausible sampling positions.
        for y in range(8):
            data[y*w*4:(y*w+60)*4] = bytes(60*4)
        self.assertIsNone(hermeswow.decode_scaled_strip(bytes(data), w, h))


if __name__ == '__main__':
    unittest.main()
