from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional
import shutil
import struct
import subprocess
import tempfile
import unittest

from pykotor.resource.formats.tpc.convert.dxt.compress_dxt import rgb_to_dxt1, rgba_to_dxt5
from pykotor.resource.formats.tpc.convert.dxt.decompress_dxt import (
    dxt1_to_rgb,
    dxt1_to_rgba,
    dxt5_to_rgba,
)
from pykotor.resource.formats.tpc.convert.rgb import rgb_to_rgba
from pykotor.resource.formats.tpc.convert.dxt.compress_dxt_ndix import (
    NDIX_COMPRESS_CLI,
    ndix_compressor_available,
)
from pykotor.resource.formats.tpc.manipulate.mipmap_ndix import _js_round, downsample_rgba_ndix
from pykotor.resource.formats.tpc.tpc_auto import bytes_tpc, read_tpc
from pykotor.resource.formats.tpc.tpc_auto import build_tpc_from_tga_bytes
from pykotor.resource.type import ResourceType
from pykotor.resource.formats.tpc.tpc_data import TPC, TPCLayer, TPCMipmap, TPCTextureFormat

# ``N_BelHd.tga`` (512×512 retail-style head texture): vendored under ``fixtures/`` for ndix vs PyKotor parity.
_N_BELHD_TGA_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "N_BelHd.tga"
# SHA256 of ``headless_export.cjs`` output for that file (ndixUR tga2tpc / ``tpc.js`` + Compressonator, May 2026).
_N_BELHD_TPC_SHA256_HEX = "80f17fe537921735f1a5714c1cdedda8c072bed3181dadb236db091a6af19c35"


def _ndix_headless_export_script() -> Optional[Path]:
    """Resolve ``vendor/ref-tga2tpc/headless_export.cjs`` from any ``Libraries/PyKotor/tests/...`` cwd."""
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        candidate = ancestor / "vendor" / "ref-tga2tpc" / "headless_export.cjs"
        if candidate.is_file():
            return candidate
    return None


def _ndix_headless_three_js(script: Path) -> Path:
    return script.parent / "node_modules" / "three" / "three.js"


def _run_headless_ndix_tpc(tga_bytes: bytes, script: Path) -> bytes:
    """Same stack as ndixUR Electron tga2tpc: ``tpc.js`` + TGALoader + Compressonator (see ``headless_export.cjs``)."""
    node = shutil.which("node")
    if not node:
        msg = "node not on PATH"
        raise AssertionError(msg)
    with tempfile.TemporaryDirectory() as tmp:
        tin = Path(tmp) / "in.tga"
        tout = Path(tmp) / "out.tpc"
        tin.write_bytes(tga_bytes)
        proc = subprocess.run(
            [node, str(script), str(tin), str(tout)],
            capture_output=True,
            timeout=180,
            check=False,
        )
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="replace")
            msg = f"headless_export failed ({proc.returncode}): {err}"
            raise AssertionError(msg)
        return tout.read_bytes()


def _pykotor_tpc_bytes_from_tga_ndix(raw_tga: bytes) -> bytes:
    """``read_tga`` → ``build_tpc_from_tga_bytes`` pipeline + ``write_tpc`` via ``bytes_tpc`` (ndix DXT when set)."""
    os.environ["PYKOTOR_DXT_COMPRESSOR"] = "ndix"
    try:
        tpc = build_tpc_from_tga_bytes(raw_tga, compression="auto", generate_mipmaps=True)
        return bytes_tpc(tpc, ResourceType.TPC)
    finally:
        os.environ.pop("PYKOTOR_DXT_COMPRESSOR", None)


def _minimal_uncompressed_tga(width: int, height: int, pixel_depth: int) -> bytes:
    """True-color type-2 TGA, top-left origin (descriptor 0x20)."""
    hdr = struct.pack(
        "<BBBHHBHHHHBB",
        0,
        0,
        2,
        0,
        0,
        0,
        0,
        0,
        width,
        height,
        pixel_depth,
        0x20,
    )
    if pixel_depth == 24:
        row = b"\xff\xff\xff" * width  # BGR white
        return hdr + row * height
    if pixel_depth == 32:
        row = b"\xff\xff\xff\xff" * width
        return hdr + row * height
    raise ValueError(pixel_depth)


def _dxt_mip_chain(width: int, height: int, tpc_format: TPCTextureFormat, seed: int) -> list[TPCMipmap]:
    """Create a complete power-of-two DXT mip chain with deterministic byte payloads."""
    mips: list[TPCMipmap] = []
    level = 0
    while True:
        mip_width = max(1, width >> level)
        mip_height = max(1, height >> level)
        mip_size = tpc_format.get_size(mip_width, mip_height)
        mips.append(
            TPCMipmap(
                mip_width,
                mip_height,
                tpc_format,
                bytearray([((seed + level) % 251) + 1]) * mip_size,
            )
        )
        if mip_width == 1 and mip_height == 1:
            return mips
        level += 1


def _animated_dxt1_tpc() -> TPC:
    """Build a 2x2 cycle texture shaped like PLC_FrcDist02.tpc."""
    tpc = TPC()
    tpc._format = TPCTextureFormat.DXT1  # noqa: SLF001
    tpc.layers = [
        TPCLayer(_dxt_mip_chain(128, 128, TPCTextureFormat.DXT1, seed))
        for seed in range(4)
    ]
    tpc.is_animated = True
    tpc.txi = (
        "proceduretype cycle\r\n"
        "numx 2\r\n"
        "numy 2\r\n"
        "fps 16\r\n"
        "blending additive\r\n"
        "downsamplemax 0\r\n"
        "downsamplemin 0"
    )
    return tpc


def _total_texture_payload_size(tpc: TPC) -> int:
    return sum(len(mipmap.data) for layer in tpc.layers for mipmap in layer.mipmaps)


class TestTPCData(unittest.TestCase):
    def setUp(self):
        self.tpc = TPC()
        # Real DXT1 block data representing actual texture patterns
        self.dxt1_red: bytes = bytes.fromhex(
            "00F800F800000000"
        )  # Pure red DXT1 block with pure red indices
        self.dxt1_gradient: bytes = bytes.fromhex("F80007E0A4A4A4A4")  # Red-green gradient
        self.dxt1_transparent: bytes = bytes.fromhex("E00700F8FFFFFFFF")

    def test_dxt1_decompression_accuracy(self):
        """Test DXT1 decompression with real texture data"""
        width, height = 4, 4
        result: bytearray = dxt1_to_rgb(self.dxt1_red, width, height)

        # Verify correct decompression of red color (5:6:5 format)
        self.assertEqual(bytes(result[0:3]), b"\xff\x00\x00")  # Red expanded from 5-bit precision
        self.assertEqual(len(result), width * height * 3)

    def test_dxt1_gradient_compression(self):
        """Test DXT1 compression with real gradient data"""
        width, height = 4, 4
        # Create a real RGB gradient
        rgb_data = bytearray()
        for y in range(height):
            for x in range(width):
                r = int((x / (width - 1)) * 248)  # 5-bit red
                g = int((y / (height - 1)) * 252)  # 6-bit green
                b = 0
                rgb_data.extend([r, g, b])

        result = rgb_to_dxt1(rgb_data, width, height)
        self.assertEqual(len(result), 8)  # Proper DXT1 block size

    def test_rgb_to_rgba_precision(self):
        """Test RGB to RGBA conversion with proper color precision"""
        # Real RGB colors with correct bit precision
        rgb_data = bytes(
            [
                0xF8,
                0xFC,
                0xF8,  # White (5:6:5)
                0x00,
                0x00,
                0x00,  # Black
                0xF8,
                0x00,
                0x00,  # Red (5-bit)
                0x00,
                0xFC,
                0x00,  # Green (6-bit)
            ]
        )

        result = rgb_to_rgba(rgb_data)
        self.assertEqual(result[0:4], b"\xf8\xfc\xf8\xff")
        self.assertEqual(result[4:8], b"\x00\x00\x00\xff")

    def test_dxt1_block_boundaries(self):
        """Test DXT1 compression across block boundaries"""
        width, height = 8, 8  # 2x2 DXT blocks
        # Create a checkered pattern crossing block boundaries
        rgb_data = bytearray()
        for y in range(height):
            for x in range(width):
                if (x + y) % 2 == 0:
                    rgb_data.extend([0xF8, 0x00, 0x00])  # Red (5-bit)
                else:
                    rgb_data.extend([0x00, 0xFC, 0x00])  # Green (6-bit)

        compressed = rgb_to_dxt1(rgb_data, width, height)
        self.assertEqual(len(compressed), 32)  # 4 DXT1 blocks

    def test_dxt1_rgba_preserves_one_bit_alpha(self):
        """DXT1 transparent blocks must decode with zero alpha."""
        result = dxt1_to_rgba(self.dxt1_transparent, 4, 4)

        self.assertEqual(len(result), 4 * 4 * 4)
        self.assertTrue(all(alpha == 0 for alpha in result[3::4]))

    def test_tpc_convert_dxt1_to_rgba_preserves_transparency(self):
        """TPC mipmap conversion must keep DXT1 cutout transparency."""
        layer = TPCLayer([TPCMipmap(4, 4, TPCTextureFormat.DXT1, bytearray(self.dxt1_transparent))])
        self.tpc.layers = [layer]
        self.tpc._format = TPCTextureFormat.DXT1  # noqa: SLF001

        self.tpc.convert(TPCTextureFormat.RGBA)

        mipmap = self.tpc.layers[0].mipmaps[0]
        self.assertEqual(mipmap.tpc_format, TPCTextureFormat.RGBA)
        self.assertTrue(all(alpha == 0 for alpha in mipmap.data[3::4]))

    def test_animated_tpc_writer_uses_retail_txi_footer_layout(self):
        """Cycle TPCs must put TXI immediately after the packed frame/mip payload."""
        tpc = _animated_dxt1_tpc()
        total_payload_size = _total_texture_payload_size(tpc)

        raw = bytes_tpc(tpc, ResourceType.TPC)

        self.assertEqual(total_payload_size, struct.unpack_from("<I", raw, 0)[0])
        self.assertEqual(1, raw[13])
        txi_offset = 0x80 + total_payload_size
        self.assertEqual(txi_offset, raw.find(b"proceduretype cycle"))
        self.assertEqual(txi_offset + len(str(tpc.txi).encode("ascii")), len(raw))
        self.assertNotIn(b"\x00", raw[txi_offset:])
        self.assertIn(b"blending additive", raw)
        self.assertNotIn(b"blending 1", raw)

        parsed = read_tpc(raw)
        self.assertTrue(parsed.is_animated)
        self.assertEqual(4, len(parsed.layers))
        self.assertEqual([8, 8, 8, 8], [len(layer.mipmaps) for layer in parsed.layers])
        self.assertEqual("cycle", parsed._txi.features.proceduretype)  # noqa: SLF001
        self.assertEqual(2, parsed._txi.features.numx)  # noqa: SLF001
        self.assertEqual(2, parsed._txi.features.numy)  # noqa: SLF001
        self.assertEqual(16.0, parsed._txi.features.fps)  # noqa: SLF001
        self.assertIn("blending additive", parsed.txi)
        self.assertNotIn("blending 1", parsed.txi)
        self.assertIn("downsamplemin 0", parsed.txi)

    def test_tpc_reader_recovers_txi_from_legacy_animated_header(self):
        """Recover TXI from old Holocron output whose header points 48 bytes before TXI."""
        tpc = _animated_dxt1_tpc()
        raw = bytearray(bytes_tpc(tpc, ResourceType.TPC))

        # Legacy writer bug: first-frame mip size * layer count and the in-memory
        # mip count were written into the header, even though the embedded TXI starts
        # after the complete packed frame/mip payload.
        struct.pack_into("<I", raw, 0, TPCTextureFormat.DXT1.get_size(128, 128) * 4)
        raw[13] = 8
        raw.extend(b"\x00")

        parsed = read_tpc(bytes(raw))

        self.assertTrue(parsed.is_animated)
        self.assertEqual(4, len(parsed.layers))
        self.assertEqual([8, 8, 8, 8], [len(layer.mipmaps) for layer in parsed.layers])
        self.assertEqual("cycle", parsed._txi.features.proceduretype)  # noqa: SLF001
        self.assertEqual(2, parsed._txi.features.numx)  # noqa: SLF001
        self.assertEqual(2, parsed._txi.features.numy)  # noqa: SLF001
        self.assertEqual(0, parsed._txi.features.downsamplemin)  # noqa: SLF001
        self.assertIn("blending additive", parsed.txi)
        self.assertNotIn("blending 1", parsed.txi)

    def test_tpc_reader_detects_full_txi_command_set_at_declared_footer(self):
        """Footer detection must not depend on a TPC-local curated TXI hint list."""
        payload = b"\x00" * TPCTextureFormat.DXT1.get_size(4, 4)
        txi = b"maxSizeHQ 64"
        raw = bytearray()
        raw.extend(struct.pack("<I", len(payload)))
        raw.extend(struct.pack("<f", 1.0))
        raw.extend(struct.pack("<H", 4))
        raw.extend(struct.pack("<H", 4))
        raw.extend(bytes([2, 2]))
        raw.extend(bytes(0x72))
        raw.extend(payload)
        raw.extend(txi)

        parsed = read_tpc(bytes(raw))

        self.assertEqual(64, parsed._txi.features.maxSizeHQ)  # noqa: SLF001
        self.assertEqual("maxSizeHQ 64", parsed.txi)

    def test_dxt5_encode_roundtrip_preserves_color_channels(self):
        """DXT5 encoder must write the color block into the final payload."""
        width, height = 4, 4
        rgba = bytes([255, 0, 0, 255] * (width * height))

        compressed = rgba_to_dxt5(rgba, width, height)
        decompressed = dxt5_to_rgba(compressed, width, height)

        self.assertEqual(len(compressed), 16)
        self.assertEqual(decompressed[3], 255)
        self.assertGreater(decompressed[0], 0)
        self.assertEqual(decompressed[1], 0)
        self.assertEqual(decompressed[2], 0)

    def test_tga2tpc_auto_32bpp_opaque_uses_dxt5(self):
        """ndixUR tga2tpc uses DXT5 for any 32-bit TGA, not DXT1 when alpha is all 0xFF."""
        raw = _minimal_uncompressed_tga(4, 4, 32)
        tpc = build_tpc_from_tga_bytes(raw, compression="auto", generate_mipmaps=False)
        self.assertEqual(tpc.format(), TPCTextureFormat.DXT5)

    def test_tga2tpc_auto_24bpp_uses_dxt1(self):
        raw = _minimal_uncompressed_tga(4, 4, 24)
        tpc = build_tpc_from_tga_bytes(raw, compression="auto", generate_mipmaps=False)
        self.assertEqual(tpc.format(), TPCTextureFormat.DXT1)

    def test_ndix_compressor_matches_standalone_node_cli(self):
        """With PYKOTOR_DXT_COMPRESSOR=ndix, DXT5 bytes match a direct ndix_compress_cli.cjs call."""
        if not ndix_compressor_available():
            self.skipTest("node or vendored ndix_compress_cli.cjs missing")
        rgba = bytes([255]) * 64
        tmp = Path(tempfile.mkdtemp()) / "rgba.raw"
        tmp.write_bytes(rgba)
        node = shutil.which("node")
        assert node is not None
        r = subprocess.run(
            [node, str(NDIX_COMPRESS_CLI), "5", "4", "4", str(tmp)],
            capture_output=True,
            check=False,
        )
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", errors="replace"))
        cli_out = r.stdout
        os.environ["PYKOTOR_DXT_COMPRESSOR"] = "ndix"
        try:
            from pykotor.resource.formats.tpc.convert.dxt.compress_dxt import rgba_to_dxt5

            py_out = bytes(rgba_to_dxt5(rgba, 4, 4))
        finally:
            os.environ.pop("PYKOTOR_DXT_COMPRESSOR", None)
        self.assertEqual(py_out, cli_out)


class TestTga2tpcNdixHeadlessParity(unittest.TestCase):
    """Byte-for-byte parity: PyKotor ``build_tpc_from_tga_bytes`` pipeline vs ndixUR ``headless_export.cjs``.

    PyKotor side: :func:`~pykotor.resource.formats.tpc.tpc_auto.build_tpc_from_tga_bytes` applies the same
    auto compression and ndix-style RGBA mips as ``vendor/ref-tga2tpc/tpc.js`` ``prepare()`` / ``generateDetailLevel``;
    DXT mip payloads use ``PYKOTOR_DXT_COMPRESSOR=ndix`` (``ndix_compress_cli.cjs``, same Compressonator defaults as the tool).

    Reference side: ``vendor/ref-tga2tpc/headless_export.cjs`` loads that ``tpc.js`` with TGALoader and the same
    ``tpc.settings`` defaults (flip off, interpolation on, compression auto, compressor normal).
    """

    @classmethod
    def setUpClass(cls):
        cls._script = _ndix_headless_export_script()
        cls._three = _ndix_headless_three_js(cls._script) if cls._script else None

    def _require_headless_stack(self) -> Path:
        if self._script is None:
            self.skipTest("headless_export.cjs not found (checkout PyKotor repo root with vendor/ref-tga2tpc)")
        if not ndix_compressor_available():
            self.skipTest("ndix DXT stack unavailable (node or ndix_compress_cli.cjs)")
        if shutil.which("node") is None:
            self.skipTest("node not on PATH")
        if self._three is None or not self._three.is_file():
            self.skipTest("run: npm --prefix vendor/ref-tga2tpc install")
        return self._script

    def _assert_tga_parity_sha256(self, raw_tga: bytes) -> None:
        script = self._require_headless_stack()
        ref = _run_headless_ndix_tpc(raw_tga, script)
        py = _pykotor_tpc_bytes_from_tga_ndix(raw_tga)
        self.assertEqual(
            hashlib.sha256(ref).digest(),
            hashlib.sha256(py).digest(),
            "SHA256 mismatch between headless ndix TPC and PyKotor TGA→TPC build (ndix DXT)",
        )
        self.assertEqual(ref, py, "TPC payloads differ after matching hash (unexpected)")

    def test_parity_4x4_32bpp_auto_dxt5_mips_sha256(self):
        raw = _minimal_uncompressed_tga(4, 4, 32)
        self._assert_tga_parity_sha256(raw)

    def test_parity_4x4_24bpp_auto_dxt1_mips_sha256(self):
        raw = _minimal_uncompressed_tga(4, 4, 24)
        self._assert_tga_parity_sha256(raw)

    def test_parity_n_belhd_tga_matches_ndix_headless_sha256(self):
        """``N_BelHd.tga`` must match ndix ``headless_export.cjs`` byte-for-byte (pinned SHA256)."""
        if not _N_BELHD_TGA_FIXTURE.is_file():
            self.skipTest(f"missing fixture {_N_BELHD_TGA_FIXTURE} (add from game/mod source)")
        raw = _N_BELHD_TGA_FIXTURE.read_bytes()
        script = self._require_headless_stack()
        ref = _run_headless_ndix_tpc(raw, script)
        py = _pykotor_tpc_bytes_from_tga_ndix(raw)
        ref_hex = hashlib.sha256(ref).hexdigest()
        py_hex = hashlib.sha256(py).hexdigest()
        self.assertEqual(
            ref_hex,
            _N_BELHD_TPC_SHA256_HEX,
            "ndix headless TPC for N_BelHd.tga changed; update _N_BELHD_TPC_SHA256_HEX if intentional",
        )
        self.assertEqual(py_hex, _N_BELHD_TPC_SHA256_HEX, "PyKotor TPC SHA256 must match ndix golden")
        self.assertEqual(ref, py, "PyKotor TPC bytes must equal ndix headless output")


class TestMipmapNdix(unittest.TestCase):
    """Regression tests for ndixUR-aligned RGBA mip generation (``mipmap_ndix``)."""

    def test_js_round_half_up_not_python_bankers(self):
        self.assertEqual(_js_round(0.5), 1)
        self.assertEqual(_js_round(2.5), 3)
        self.assertEqual(round(2.5), 2)

    def test_bicubic_2x2_to_1x1_is_all_zero_like_js(self):
        parent = bytearray([10, 20, 30, 255] * 4)
        for interp in (True, False):
            with self.subTest(interpolation=interp):
                out = downsample_rgba_ndix(parent, 2, 2, 1, interpolation=interp)
                self.assertEqual(out, bytearray(4))

    def test_box_filter_even_parent_4x4_to_2x2(self):
        parent = bytearray([255, 0, 0, 255] * 16)
        out = downsample_rgba_ndix(parent, 4, 4, 1, interpolation=False)
        self.assertEqual(out, bytearray([255, 0, 0, 255] * 4))


if __name__ == "__main__":
    unittest.main()
