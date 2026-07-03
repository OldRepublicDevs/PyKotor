"""TPC binary reader/writer implementation."""

from __future__ import annotations

import itertools
import math

from typing import TYPE_CHECKING

import kaitaistruct

from bioware_kaitai_formats.tpc import Tpc

from pykotor.common.stream import BinaryReader
from pykotor.resource.formats.tpc.manipulate.rotate import rotate_rgb_rgba  # noqa: F401
from pykotor.resource.formats.tpc.tpc_data import TPC, TPCLayer, TPCMipmap, TPCTextureFormat
from pykotor.resource.type import ResourceReader, ResourceWriter, autoclose

if TYPE_CHECKING:
    from typing_extensions import Literal  # pyright: ignore[reportMissingModuleSource]

    from pykotor.resource.formats.txi.txi_data import TXI
    from pykotor.resource.type import SOURCE_TYPES, TARGET_TYPES


def swizzle(
    data: bytes | bytearray,
    width: int,
    height: int,
    bytes_per_pixel: int,
) -> bytearray:
    """Swizzle pixel data for GPU-friendly access patterns."""

    def swizzle_offset(x: int, y: int, w: int, h: int) -> int:
        log2_w = int(math.log2(w))
        log2_h = int(math.log2(h))
        offset = 0
        shift = 0
        while log2_w or log2_h:
            if log2_w:
                offset |= (x & 1) << shift
                x >>= 1
                shift += 1
                log2_w -= 1
            if log2_h:
                offset |= (y & 1) << shift
                y >>= 1
                shift += 1
                log2_h -= 1
        return offset

    swizzled = bytearray(width * height * bytes_per_pixel)
    for y, x in itertools.product(range(height), range(width)):
        src_offset = (y * width + x) * bytes_per_pixel
        dst_offset = swizzle_offset(x, y, width, height) * bytes_per_pixel
        swizzled[dst_offset : dst_offset + bytes_per_pixel] = data[
            src_offset : src_offset + bytes_per_pixel
        ]
    return swizzled


def deswizzle(data: bytes | bytearray, width: int, height: int, bytes_per_pixel: int) -> bytearray:
    """Deswizzle pixel data from GPU-friendly layout to linear layout."""

    def deswizzle_offset(x: int, y: int, w: int, h: int) -> int:
        log2_w = int(math.log2(w))
        log2_h = int(math.log2(h))
        offset = 0
        shift = 0
        while log2_w or log2_h:
            if log2_w:
                offset |= (x & 1) << shift
                x >>= 1
                shift += 1
                log2_w -= 1
            if log2_h:
                offset |= (y & 1) << shift
                y >>= 1
                shift += 1
                log2_h -= 1
        return offset

    deswizzled = bytearray(width * height * bytes_per_pixel)
    for y, x in itertools.product(range(height), range(width)):
        src_offset = deswizzle_offset(x, y, width, height) * bytes_per_pixel
        dst_offset = (y * width + x) * bytes_per_pixel
        deswizzled[dst_offset : dst_offset + bytes_per_pixel] = data[
            src_offset : src_offset + bytes_per_pixel
        ]
    return deswizzled


class TPCBinaryReader(ResourceReader):
    """Used to read TPC binary data.

    TPC (Texture Pack Container) files store texture data with mipmaps, compression,
    and various texture formats used throughout KotOR.

    Observed retail behavior:
    ----------
        KotOR serves in-game textures from ``TPC`` payloads with mip chains and the encodings
        enumerated in ``tpc_data`` (DXT and uncompressed RGB/RGBA variants).

        Note: TPC (Texture Pack Container) files store texture data with mipmaps, compression,
        and various texture formats (DXT1, DXT5, RGB, RGBA) used in KotOR ``.tpc`` payloads.

    """

    MAX_DIMENSIONS: Literal[0x8000] = 0x8000
    IMG_DATA_START_OFFSET: Literal[0x80] = 0x80

    def __init__(
        self,
        source: SOURCE_TYPES,
        offset: int = 0,
        size: int | None = None,
    ):
        super().__init__(source, offset, size)

    @autoclose
    def load(self, *, auto_close: bool = True) -> TPC:  # noqa: PLR0912, C901, PLR0915
        data = self._reader.read_all()
        # The Kaitai parser is used as a best-effort structural smoke test only.
        # PyKotor's manual reader below is authoritative because retail TPCs,
        # especially animated textures with embedded TXI footers, use header
        # combinations that generated parsers have historically misinterpreted.
        try:
            Tpc.from_bytes(data)
        except (kaitaistruct.KaitaiStructError, UnicodeDecodeError, OSError, ValueError):
            pass
        self._reader = BinaryReader.from_bytes(data, 0)

        self._tpc: TPC = TPC()
        self._layer_count: int = 1
        self._mipmap_count: int = 0

        header_data_size: int = self._reader.read_uint32()  # 0x0
        data_size: int = header_data_size
        compressed: bool = header_data_size != 0
        alpha_test: float = self._reader.read_single()  # 0x4
        self._tpc.alpha_test = alpha_test
        width: int = self._reader.read_uint16()  # 0x8
        height: int = self._reader.read_uint16()  # 0xA

        if max(width, height) >= self.MAX_DIMENSIONS:
            raise ValueError(f"Unsupported image dimensions ({width}x{height})")

        pixel_type, self._mipmap_count = (
            self._reader.read_uint8(),
            self._reader.read_uint8(),
        )
        # Compressed: KotOR TPC uses 0x02=DXT1 and 0x04=DXT5 only (see xoreos tpc.cpp).
        tpc_format: TPCTextureFormat = {
            (True, 2): TPCTextureFormat.DXT1,
            (True, 4): TPCTextureFormat.DXT5,
            (False, 1): TPCTextureFormat.Greyscale,
            (False, 2): TPCTextureFormat.RGB,
            (False, 4): TPCTextureFormat.RGBA,
            (False, 12): TPCTextureFormat.BGRA,
        }.get((compressed, pixel_type), TPCTextureFormat.Invalid)
        if tpc_format == TPCTextureFormat.Invalid:
            raise ValueError(
                f"Unsupported texture format (pixel_type: {pixel_type}, compressed: {compressed})"
            )
        self._tpc._format = tpc_format  # noqa: SLF001

        total_cube_sides: Literal[6] = 6
        if not compressed:
            data_size = tpc_format.get_size(width, height)

        elif (
            height != 0  # noqa: PLR2004
            and width != 0
            and int(height / width) == total_cube_sides
        ):
            self._tpc.is_cube_map = True
            height = int(height / total_cube_sides)
            self._layer_count = total_cube_sides

        complete_data_size: int = data_size
        for level in range(1, self._mipmap_count):
            reduced_width = max(width >> level, 1)
            reduced_height = max(height >> level, 1)
            complete_data_size += tpc_format.get_size(reduced_width, reduced_height)

        complete_data_size *= self._layer_count
        texture_data_size = self._select_texture_data_size(
            data,
            default_size=complete_data_size,
            declared_size=header_data_size,
        )

        self._reader.seek(min(self.IMG_DATA_START_OFFSET + texture_data_size, self._reader.size()))
        txi_data_size: int = self._reader.size() - self._reader.position()
        if txi_data_size > 0:
            txi_text = self._reader.read_string(
                txi_data_size,
                encoding="ascii",
                errors="ignore",
            ).split("\x00", maxsplit=1)[0]
            if txi_text.strip():
                self._tpc.txi = txi_text

        txi_data: TXI = self._tpc._txi  # noqa: SLF001
        self._tpc.is_animated = bool(
            self._tpc.txi
            and self._tpc.txi.strip()
            and txi_data.features.proceduretype
            and txi_data.features.proceduretype.lower() == "cycle"  # noqa: SLF001
            and txi_data.features.numx
            and txi_data.features.numy
            and txi_data.features.fps,
        )

        if self._tpc.is_animated:
            animated_txi: TXI = self._tpc._txi  # noqa: SLF001
            self._layer_count = (animated_txi.features.numx or 1) * (
                animated_txi.features.numy or 1
            )
            width //= animated_txi.features.numx or 1
            height //= animated_txi.features.numy or 1
            data_size //= self._layer_count
            animated_width = width
            animated_height = height
            self._mipmap_count = 0
            while animated_width > 0 and animated_height > 0:
                animated_width //= 2
                animated_height //= 2
                self._mipmap_count += 1

        if compressed and not self._tpc.is_animated:
            expected_size: int = tpc_format.get_size(width, height)
            if data_size < expected_size:
                raise ValueError(
                    f"Invalid data size for a texture of {width}x{height} pixels and format {tpc_format!r}"
                )
            data_size = expected_size

        self._reader.seek(self.IMG_DATA_START_OFFSET)
        if (
            width <= 0
            or height <= 0
            or width >= self.MAX_DIMENSIONS
            or height >= self.MAX_DIMENSIONS
        ):
            raise ValueError(f"Invalid dimensions ({width}x{height}) for format {tpc_format!r}")

        full_image_data_size: int = tpc_format.get_size(
            width,
            height,
        )
        full_data_size: int = texture_data_size
        if full_data_size < (self._layer_count * full_image_data_size):
            msg: str = f"Insufficient data for image. Expected at least {hex(self._layer_count * full_image_data_size)} bytes, but only {hex(full_data_size)} bytes are available."
            raise ValueError(
                msg,
            )

        for _ in range(self._layer_count):
            layer = TPCLayer()
            self._tpc.layers.append(layer)
            layer_width, layer_height = width, height
            layer_size: int = (
                tpc_format.get_size(layer_width, layer_height)
                if self._tpc.is_animated
                else data_size
            )

            for _ in range(self._mipmap_count):
                mm_width, mm_height = (
                    max(1, layer_width),
                    max(1, layer_height),
                )
                mm_size: int = max(
                    layer_size,
                    tpc_format.min_size(),
                )
                mm = TPCMipmap(
                    width=mm_width,
                    height=mm_height,
                    tpc_format=tpc_format,
                    data=bytearray(self._reader.read_bytes(mm_size)),
                )
                layer.mipmaps.append(mm)

                if full_data_size <= mm_size or mm_size < tpc_format.get_size(mm_width, mm_height):
                    break

                full_data_size -= mm_size
                layer_width, layer_height = (
                    layer_width >> 1,
                    layer_height >> 1,
                )
                layer_size = tpc_format.get_size(
                    layer_width,
                    layer_height,
                )

                if layer_width < 1 and layer_height < 1:
                    break

        if self._tpc.format() == TPCTextureFormat.BGRA:
            for layer in self._tpc.layers:
                for mipmap in layer.mipmaps:
                    mipmap.data = deswizzle(
                        mipmap.data,
                        mipmap.width,
                        mipmap.height,
                        self._tpc.format().bytes_per_pixel(),
                    )

        if self._tpc.is_cube_map:
            self._normalize_cubemaps()

        return self._tpc

    def _select_texture_data_size(
        self,
        data: bytes,
        *,
        default_size: int,
        declared_size: int,
    ) -> int:
        """Choose the texture payload length before an embedded TXI footer.

        Some TPC writers store only the first mip level size in the compressed
        ``data_size`` header field, while others store the full texture payload
        size.  Prefer offsets that actually look like an ASCII TXI footer; fall
        back to PyKotor's historical size calculation otherwise.
        """
        candidates: list[int] = []
        if declared_size > 0:
            candidates.append(declared_size)
            if self._layer_count > 1:
                candidates.append(declared_size * self._layer_count)
        candidates.append(default_size)

        seen: set[int] = set()
        for size in candidates:
            if size in seen:
                continue
            seen.add(size)
            txi_offset = self.IMG_DATA_START_OFFSET + size
            if self._looks_like_txi_at(data, txi_offset):
                return size

            shifted_size = self._find_nearby_txi_size(data, size)
            if shifted_size is not None:
                return shifted_size
        return default_size

    def _find_nearby_txi_size(self, data: bytes, size: int) -> int | None:
        # Legacy animated output can undercount the packed frame/mip payload by
        # a few DXT min-size blocks because the header mip count was written for
        # per-frame mips before the TXI footer had been parsed.
        txi_offset = self.IMG_DATA_START_OFFSET + size
        scan_end = min(len(data), txi_offset + 1024)
        for offset in range(txi_offset + 1, scan_end):
            if self._looks_like_txi_at(data, offset):
                return offset - self.IMG_DATA_START_OFFSET
        return None

    @classmethod
    def _looks_like_txi_at(cls, data: bytes, offset: int) -> bool:
        if offset < cls.IMG_DATA_START_OFFSET or offset >= len(data):
            return False

        footer = data[offset:]
        raw = footer.split(b"\x00", maxsplit=1)[0].lstrip(b"\r\n\t ")
        if not raw:
            return False

        first_byte = raw[0]
        if not (65 <= first_byte <= 90 or 97 <= first_byte <= 122):
            return False

        sample = raw[:4096]
        if any(byte < 32 and byte not in b"\r\n\t" for byte in sample):
            return False

        try:
            text = raw.decode("ascii").strip()
        except UnicodeDecodeError:
            return False

        from pykotor.resource.formats.txi.txi_data import TXI

        return TXI.looks_like_txi(text)

    def _normalize_cubemaps(self):
        self._tpc.convert(
            TPCTextureFormat.RGB
            if self._tpc.format() == TPCTextureFormat.DXT1
            else TPCTextureFormat.RGBA
        )
        rotation: tuple[int, int, int, int, int, int] = (3, 1, 0, 2, 2, 0)
        for i, layer in enumerate(self._tpc.layers):
            for mipmap in layer.mipmaps:
                rotate_rgb_rgba(
                    mipmap.data,
                    mipmap.width,
                    mipmap.height,
                    self._tpc.format().bytes_per_pixel(),
                    rotation[i],
                )
        self._tpc.layers[0].mipmaps, self._tpc.layers[1].mipmaps = (
            self._tpc.layers[1].mipmaps,
            self._tpc.layers[0].mipmaps,
        )


class TPCBinaryWriter(ResourceWriter):
    """Used to write TPC instances as TPC binary data.

    File Structure:
    Header (128 bytes):
    - 0x00-0x03: Data size (uint32 LE) - 0 if uncompressed
    - 0x04-0x07: Alpha blend float (4 bytes)
    - 0x08-0x09: Width (uint16 LE)
    - 0x0A-0x0B: Height (uint16 LE)
    - 0x0C: Encoding (uint8)
    - 0x0D: Mipmap count (uint8)
    - 0x0E-0x7F: Reserved (114 bytes)

    Followed by:
    - Texture data
    - TXI data (optional)

    Observed retail behavior:
    ----------
        Writer output targets the same header layout and mip packing that KotOR loads at runtime.

    """

    MAX_DIMENSIONS: Literal[0x8000] = TPCBinaryReader.MAX_DIMENSIONS
    IMG_DATA_START_OFFSET: Literal[0x80] = TPCBinaryReader.IMG_DATA_START_OFFSET

    # Encoding values from TPCObject.cs
    ENCODING_GRAY: Literal[0x01] = 0x01
    ENCODING_RGB: Literal[0x02] = 0x02
    ENCODING_RGBA: Literal[0x04] = 0x04
    ENCODING_BGRA: Literal[0x0C] = 0x0C

    def __init__(self, tpc: TPC, target: TARGET_TYPES):
        super().__init__(target)
        self._tpc: TPC = tpc
        self._layer_count: int = len(tpc.layers) if tpc.layers else 0
        self._mipmap_count: int = (
            len(tpc.layers[0].mipmaps) if tpc.layers and tpc.layers[0].mipmaps else 0
        )

    def _get_pixel_encoding(self, tpc_format: TPCTextureFormat) -> int:
        """Get the pixel encoding value for the given format."""
        if tpc_format == TPCTextureFormat.Greyscale:
            return self.ENCODING_GRAY
        if tpc_format in (TPCTextureFormat.RGB, TPCTextureFormat.DXT1):
            return self.ENCODING_RGB
        if tpc_format in (TPCTextureFormat.RGBA, TPCTextureFormat.DXT5):
            return self.ENCODING_RGBA
        if tpc_format == TPCTextureFormat.BGRA:
            return self.ENCODING_BGRA
        raise ValueError(f"Invalid TPC texture format: {tpc_format}")

    def _validate_dimensions(
        self,
        width: int,
        height: int,
    ) -> None:
        """Validate the texture dimensions."""
        if width <= 0 or height <= 0:
            raise ValueError(f"Invalid dimensions: {width}x{height}")
        if width >= self.MAX_DIMENSIONS or height >= self.MAX_DIMENSIONS:
            raise ValueError(f"Dimensions exceed maximum allowed: {width}x{height}")

    @autoclose
    def write(self, *, auto_close: bool = True):  # noqa: FBT001, FBT002, ARG002  # pyright: ignore[reportUnusedParameters]
        """Write the TPC data to the target."""
        if not self._tpc.layers:
            raise ValueError("TPC contains no layers")

        frame_width, frame_height = self._tpc.dimensions()
        tpc_format: TPCTextureFormat = self._tpc.format()

        # Validate dimensions
        self._validate_dimensions(frame_width, frame_height)

        # Handle animated textures and cubemaps
        layer_width: int = frame_width
        layer_height: int = frame_height
        width: int = frame_width
        height: int = frame_height

        if self._tpc.is_animated:
            txi: TXI = self._tpc._txi  # noqa: SLF001
            numx: int = max(1, txi.features.numx or 0)
            numy: int = max(1, txi.features.numy or 0)
            if numx * numy != self._layer_count and self._layer_count:
                numx = self._layer_count
                numy = 1
            elif self._layer_count == 0:
                self._layer_count = max(1, numx * numy)
            width = frame_width * max(1, numx)
            height = frame_height * max(1, numy)
            layer_width = frame_width
            layer_height = frame_height
            if layer_width <= 0 or layer_height <= 0:
                raise ValueError(
                    f"Invalid layer dimensions ({layer_width}x{layer_height}) for animated texture"
                )

        elif self._tpc.is_cube_map:
            if self._layer_count != 6:  # noqa: PLR2004
                raise ValueError(f"Cubemap must have exactly 6 layers, found {self._layer_count}")
            height = frame_height * self._layer_count

        # Calculate data size.  For compressed TPCs this header field is the
        # texture payload length before any embedded TXI footer.
        data_size: int = 0
        if tpc_format.is_dxt():
            data_size = sum(
                len(layer.mipmaps[mipmap_idx].data)
                for layer in self._tpc.layers
                for mipmap_idx in range(min(self._mipmap_count, len(layer.mipmaps)))
            )

        header_mipmap_count: int = 1 if self._tpc.is_animated else self._mipmap_count

        # Write header (128 bytes)
        pixel_encoding: int = self._get_pixel_encoding(tpc_format)
        self._writer.write_uint32(data_size)  # 0x00-0x03: Data size (0 when uncompressed)
        self._writer.write_single(self._tpc.alpha_test)  # 0x04-0x07: Alpha blending threshold
        self._writer.write_uint16(width)  # 0x08-0x09: Width
        self._writer.write_uint16(height)  # 0x0A-0x0B: Height
        self._writer.write_uint8(pixel_encoding)  # 0x0C: Pixel encoding
        self._writer.write_uint8(header_mipmap_count)  # 0x0D: Mipmap count
        self._writer.write_bytes(bytes(0x72))  # 0x0E-0x7F: Reserved padding

        # Write texture data for each layer
        for layer in self._tpc.layers:
            for mipmap_idx in range(self._mipmap_count):
                if mipmap_idx >= len(layer.mipmaps):
                    break

                mipmap: TPCMipmap = layer.mipmaps[mipmap_idx]
                current_width = max(1, layer_width >> mipmap_idx)
                current_height = max(1, layer_height >> mipmap_idx)

                if mipmap.width != current_width or mipmap.height != current_height:
                    raise ValueError(
                        f"Invalid mipmap dimensions at level {mipmap_idx}."  # noqa: E501
                        f" Expected {current_width}x{current_height},"
                        f" got {mipmap.width}x{mipmap.height}",
                    )

                mipmap_data: bytearray = mipmap.data
                if (
                    tpc_format == TPCTextureFormat.BGRA
                    and (current_width & (current_width - 1)) == 0
                ):
                    mipmap_data = swizzle(
                        mipmap_data, current_width, current_height, tpc_format.bytes_per_pixel()
                    )

                self._writer.write_bytes(bytes(mipmap_data))

        # Rest of the file format is ascii TXI data.
        if not str(self._tpc.txi).strip():
            return

        txi_payload: str = str(self._tpc.txi).strip().replace("\r\n", "\n").replace("\r", "\n")
        if not txi_payload:
            return
        txi_lines = txi_payload.split("\n")
        normalized = "\r\n".join(txi_lines)
        self._writer.write_bytes(normalized.encode("ascii", errors="ignore"))
