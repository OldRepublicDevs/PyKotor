"""This module handles classes relating to editing TPC files.

TPC is KotOR's proprietary texture format supporting various compression and color formats.

Observed retail behavior:
----------
        KotOR loads textures from ``.tpc`` resources (and related sidecar metadata) using the
        proprietary header + mip chain layout this package models. It has been observed that
        retail ``.tpc`` data uses DXT1/DXT5 and uncompressed RGB/RGBA payloads consistent with
        other Aurora-era titles.

"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import IntEnum, auto
from typing import TYPE_CHECKING

from pykotor.resource.formats._base import BiowareResource, ComparableMixin
from pykotor.resource.formats.tpc.convert.bgra import (
    bgr_to_bgra,
    bgr_to_grey,
    bgr_to_rgb,
    bgr_to_rgba,
    bgra_to_bgr,
    bgra_to_grey,
    bgra_to_rgb,
    bgra_to_rgba,
    rgb_to_bgr,
    rgb_to_bgra,
    rgba_to_bgra,
)
from pykotor.resource.formats.tpc.convert.dxt.compress_dxt import (
    rgb_to_dxt1,
    rgba_to_dxt3,
    rgba_to_dxt5,
)
from pykotor.resource.formats.tpc.convert.dxt.decompress_dxt import (
    dxt1_to_rgb,
    dxt1_to_rgba,
    dxt3_to_rgba,
    dxt5_to_rgba,
)
from pykotor.resource.formats.tpc.convert.rgb import (
    grey_to_rgb,
    grey_to_rgba,
    rgb_to_grey,
    rgb_to_rgba,
    rgba_to_grey,
    rgba_to_rgb,
)
from pykotor.resource.formats.tpc.manipulate.downsample import downsample_rgb
from pykotor.resource.formats.tpc.manipulate.dxt_manipulate import (
    flip_horizontally_dxt,
    flip_vertically_dxt,
    rotate_dxt1,
    rotate_dxt5,
)
from pykotor.resource.formats.tpc.manipulate.rotate import (
    flip_horizontally_rgb_rgba,
    flip_vertically_rgb_rgba,
    rotate_rgb_rgba,
)
from pykotor.resource.formats.txi.txi_data import TXI
from pykotor.resource.type import ResourceType

if TYPE_CHECKING:
    from PIL import Image
    from qtpy.QtGui import QIcon, QImage, QPixmap
    from typing_extensions import Literal, Self  # pyright: ignore[reportMissingModuleSource]


class TPCTextureFormat(IntEnum):
    """Enumeration of supported TPC texture formats."""

    Invalid = -1
    Greyscale = 0
    DXT1 = auto()
    DXT3 = auto()
    DXT5 = auto()
    RGB = auto()
    RGBA = auto()
    BGRA = auto()
    BGR = auto()

    def block_size(self) -> Literal[1, 4]:
        """Get the block size for this format."""
        return 4 if self.is_dxt() else 1

    def bytes_per_pixel(self) -> Literal[1, 3, 4]:
        """Get the number of bytes per pixel for this format."""
        if self is self.Greyscale:
            return 1
        if self.is_dxt():
            return 1  # technically incorrect, but used for size calculations
        if self in (self.RGB, self.BGR):
            return 3
        if self in (self.RGBA, self.BGRA):
            return 4
        return 1  # Default fallback for Invalid or unknown formats

    def is_dxt(self) -> bool:
        """Check if this format is a DXT compression format."""
        return self in (
            self.DXT1,
            self.DXT3,
            self.DXT5,
        )

    def bytes_per_block(self) -> Literal[1, 8, 16]:
        """Get the number of bytes per block for this format."""
        if not self.is_dxt():
            return 1
        return 8 if self is self.DXT1 else 16

    def min_size(self) -> Literal[0, 1, 3, 4, 8, 16]:
        """Get the minimum size in bytes for this format."""
        min_size: int = 0
        if self is self.Greyscale:
            min_size = 1
        elif self in (self.RGB, self.BGR):
            min_size = 3
        elif self in (self.RGBA, self.BGRA):
            min_size = 4
        elif self.is_dxt():
            min_size = self.bytes_per_block()
        return min_size

    def get_size(self, width: int, height: int) -> int:
        """Calculate the size in bytes needed for an image of the given dimensions."""
        size: int = 0
        if self.is_dxt():
            bytes_per_block = self.bytes_per_block()
            size = ((width + 3) // 4) * ((height + 3) // 4) * bytes_per_block
        else:
            size = width * height * self.bytes_per_pixel()
        return max(self.min_size(), size)

    def to_qimage_format(self) -> QImage.Format:
        """Convert to Qt image format.

        Note: BGRA format should be converted to RGBA before calling this method,
        as Qt does not have a native BGRA format. Format_ARGB32 expects ARGB byte order
        (A, R, G, B), which does not match BGRA byte order (B, G, R, A).

        Raises:
            ValueError: If format is BGRA (must be converted to RGBA first) or Invalid/unsupported.
        """
        from qtpy.QtGui import QImage

        q_format: QImage.Format = QImage.Format.Format_Invalid
        if self is self.Greyscale:
            q_format = QImage.Format.Format_Grayscale8
        elif self is self.RGB:
            q_format = QImage.Format.Format_RGB888
        elif self is self.BGR:
            q_format = QImage.Format.Format_BGR888
        elif self is self.RGBA:
            q_format = QImage.Format.Format_RGBA8888
        elif self is self.BGRA:
            # BGRA cannot be directly mapped to a Qt format
            # Format_ARGB32 expects ARGB byte order (A, R, G, B)
            # but BGRA is (B, G, R, A) - completely different byte order
            # Callers must convert BGRA to RGBA first using bgra_to_rgba()
            raise ValueError(
                "BGRA format cannot be directly converted to QImage format. Convert to RGBA first using bgra_to_rgba() or mipmap.convert(TPCTextureFormat.RGBA)."
            )
        elif self is self.Invalid:
            q_format = QImage.Format.Format_Invalid
        else:
            raise ValueError(f"Unsupported format: {self!r}")
        return q_format

    @classmethod
    def from_qimage_format(cls, qimage_format: QImage.Format) -> Self:
        """Create from Qt image format."""
        from qtpy.QtGui import QImage

        fmt = cls.Invalid
        if qimage_format == QImage.Format.Format_Grayscale8:
            fmt = cls.Greyscale
        elif qimage_format == QImage.Format.Format_RGB888:
            fmt = cls.RGB
        elif qimage_format == QImage.Format.Format_BGR888:
            fmt = cls.BGR
        elif qimage_format == QImage.Format.Format_RGBA8888:
            fmt = cls.RGBA
        elif qimage_format == QImage.Format.Format_ARGB32:
            fmt = cls.BGRA
        return fmt  # type: ignore[return-value]

    def to_pil_mode(self) -> str:
        """Convert to PIL image mode."""
        mode: Literal["", "L", "RGB", "RGBA"] = ""
        if self is self.Greyscale:
            mode = "L"
        elif self is self.RGB:
            mode = "RGB"
        elif self is self.RGBA:
            mode = "RGBA"
        return mode

    @classmethod
    def from_pil_mode(cls, mode: Literal["L", "RGB", "RGBA"]) -> Self:
        """Create from PIL image mode."""
        fmt = cls.Invalid
        if mode == "L":
            fmt = cls.Greyscale
        elif mode == "RGB":
            fmt = cls.RGB
        elif mode == "RGBA":
            fmt = cls.RGBA
        return fmt  # type: ignore[return-value]


@dataclass
class TPCMipmap(ComparableMixin):
    """A single mipmap level in a TPC texture."""

    width: int
    height: int
    tpc_format: TPCTextureFormat
    data: bytearray

    @property
    def size(self) -> int:
        """Get the size of the mipmap data in bytes."""
        return len(self.data)

    def to_qicon(self) -> QIcon:
        """Convert to Qt icon."""
        from qtpy.QtGui import QIcon, QPixmap, QTransform

        pixmap: QPixmap = QPixmap.fromImage(self.to_qimage()).transformed(QTransform().scale(1, -1))
        return QIcon(pixmap)

    def to_qimage(self) -> QImage:
        """Convert to Qt image.

        Note: BGRA format is automatically converted to RGBA before creating QImage,
        as Qt does not have a native BGRA format.
        """
        from qtpy.QtGui import QImage

        # BGRA must be converted to RGBA for Qt compatibility
        if self.tpc_format == TPCTextureFormat.BGRA:
            # Create a copy and convert to RGBA
            mipmap_copy = self.copy()
            mipmap_copy.convert(TPCTextureFormat.RGBA)
            return QImage(
                bytes(mipmap_copy.data),
                mipmap_copy.width,
                mipmap_copy.height,
                mipmap_copy.tpc_format.to_qimage_format(),
            )

        return QImage(bytes(self.data), self.width, self.height, self.tpc_format.to_qimage_format())

    def to_pil_image(self) -> Image.Image:
        """Convert to PIL image."""
        from PIL import Image

        return Image.frombytes(self.tpc_format.to_pil_mode(), (self.width, self.height), self.data)

    def convert(self, target: TPCTextureFormat):
        """Convert the mipmap to a different format."""
        if self.tpc_format == target:
            return

        if self.tpc_format == TPCTextureFormat.RGBA:
            if target is TPCTextureFormat.BGR:
                self.data = rgb_to_bgr(rgba_to_rgb(self.data))
            elif target is TPCTextureFormat.RGB:
                self.data = rgba_to_rgb(self.data)
            elif target is TPCTextureFormat.BGRA:
                self.data = rgba_to_bgra(self.data)
            elif target is TPCTextureFormat.Greyscale:
                self.data = rgba_to_grey(self.data)
            elif target is TPCTextureFormat.DXT1:
                self.data = rgb_to_dxt1(rgba_to_rgb(self.data), self.width, self.height)
            elif target is TPCTextureFormat.DXT3:
                self.data = rgba_to_dxt3(self.data, self.width, self.height)
            elif target is TPCTextureFormat.DXT5:
                self.data = rgba_to_dxt5(self.data, self.width, self.height)

        elif self.tpc_format == TPCTextureFormat.RGB:
            if target is TPCTextureFormat.BGR:
                self.data = rgb_to_bgr(self.data)
            elif target is TPCTextureFormat.RGBA:
                self.data = rgb_to_rgba(self.data)
            elif target is TPCTextureFormat.BGRA:
                self.data = rgb_to_bgra(self.data)
            elif target is TPCTextureFormat.Greyscale:
                self.data = rgb_to_grey(self.data)
            elif target is TPCTextureFormat.DXT1:
                self.data = rgb_to_dxt1(self.data, self.width, self.height)
            elif target is TPCTextureFormat.DXT3:
                self.data = rgba_to_dxt3(rgb_to_rgba(self.data), self.width, self.height)
            elif target is TPCTextureFormat.DXT5:
                self.data = rgba_to_dxt5(rgb_to_rgba(self.data), self.width, self.height)

        elif self.tpc_format == TPCTextureFormat.BGR:
            if target is TPCTextureFormat.RGB:
                self.data = bgr_to_rgb(self.data)
            elif target is TPCTextureFormat.RGBA:
                self.data = bgr_to_rgba(self.data)
            elif target is TPCTextureFormat.BGRA:
                self.data = bgr_to_bgra(self.data)
            elif target is TPCTextureFormat.Greyscale:
                self.data = bgr_to_grey(self.data)
            elif target is TPCTextureFormat.DXT1:
                self.data = rgb_to_dxt1(bgr_to_rgb(self.data), self.width, self.height)
            elif target is TPCTextureFormat.DXT3:
                self.data = rgba_to_dxt3(bgr_to_rgba(self.data), self.width, self.height)
            elif target is TPCTextureFormat.DXT5:
                self.data = rgba_to_dxt5(bgr_to_rgba(self.data), self.width, self.height)

        elif self.tpc_format == TPCTextureFormat.BGRA:
            if target is TPCTextureFormat.BGR:
                self.data = bgra_to_bgr(self.data)
            elif target is TPCTextureFormat.RGB:
                self.data = bgra_to_rgb(self.data)
            elif target is TPCTextureFormat.RGBA:
                self.data = bgra_to_rgba(self.data)
            elif target is TPCTextureFormat.Greyscale:
                self.data = bgra_to_grey(self.data)
            elif target is TPCTextureFormat.DXT1:
                self.data = rgb_to_dxt1(bgra_to_rgb(self.data), self.width, self.height)
            elif target is TPCTextureFormat.DXT3:
                self.data = rgba_to_dxt3(bgra_to_rgba(self.data), self.width, self.height)
            elif target is TPCTextureFormat.DXT5:
                self.data = rgba_to_dxt5(bgra_to_rgba(self.data), self.width, self.height)

        elif self.tpc_format == TPCTextureFormat.Greyscale:
            if target is TPCTextureFormat.BGR:
                self.data = rgb_to_bgr(grey_to_rgb(self.data))
            elif target is TPCTextureFormat.RGB:
                self.data = grey_to_rgb(self.data)
            elif target is TPCTextureFormat.RGBA:
                self.data = grey_to_rgba(self.data)
            elif target is TPCTextureFormat.BGRA:
                self.data = rgba_to_bgra(grey_to_rgba(self.data))
            elif target is TPCTextureFormat.DXT1:
                self.data = rgb_to_dxt1(grey_to_rgb(self.data), self.width, self.height)
            elif target is TPCTextureFormat.DXT3:
                self.data = rgba_to_dxt3(grey_to_rgba(self.data), self.width, self.height)
            elif target is TPCTextureFormat.DXT5:
                self.data = rgba_to_dxt5(grey_to_rgba(self.data), self.width, self.height)

        elif self.tpc_format == TPCTextureFormat.DXT1:
            rgb_data: bytearray = dxt1_to_rgb(self.data, self.width, self.height)
            if target is TPCTextureFormat.BGR:
                self.data = rgb_to_bgr(rgb_data)
            elif target is TPCTextureFormat.RGB:
                self.data = rgb_data
            elif target is TPCTextureFormat.RGBA:
                dxt1_rgba_data = dxt1_to_rgba(self.data, self.width, self.height)
                self.data = dxt1_rgba_data
            elif target is TPCTextureFormat.BGRA:
                dxt1_rgba_data = dxt1_to_rgba(self.data, self.width, self.height)
                self.data = rgba_to_bgra(dxt1_rgba_data)
            elif target is TPCTextureFormat.Greyscale:
                self.data = rgb_to_grey(rgb_data)
            elif target is TPCTextureFormat.DXT3:
                dxt1_rgba_data = dxt1_to_rgba(self.data, self.width, self.height)
                self.data = rgba_to_dxt3(dxt1_rgba_data, self.width, self.height)
            elif target is TPCTextureFormat.DXT5:
                dxt1_rgba_data = dxt1_to_rgba(self.data, self.width, self.height)
                self.data = rgba_to_dxt5(dxt1_rgba_data, self.width, self.height)

        elif self.tpc_format == TPCTextureFormat.DXT3:
            rgba_data: bytearray = dxt3_to_rgba(self.data, self.width, self.height)
            if target is TPCTextureFormat.BGR:
                self.data = rgb_to_bgr(rgba_to_rgb(rgba_data))
            elif target is TPCTextureFormat.RGB:
                self.data = rgba_to_rgb(rgba_data)
            elif target is TPCTextureFormat.RGBA:
                self.data = rgba_data
            elif target is TPCTextureFormat.BGRA:
                self.data = rgba_to_bgra(rgba_data)
            elif target is TPCTextureFormat.Greyscale:
                self.data = rgba_to_grey(rgba_data)
            elif target is TPCTextureFormat.DXT1:
                self.data = rgb_to_dxt1(rgba_to_rgb(rgba_data), self.width, self.height)
            elif target is TPCTextureFormat.DXT5:
                self.data = rgba_to_dxt5(rgba_data, self.width, self.height)

        elif self.tpc_format == TPCTextureFormat.DXT5:
            rgba_data = dxt5_to_rgba(self.data, self.width, self.height)
            if target is TPCTextureFormat.BGR:
                self.data = rgb_to_bgr(rgba_to_rgb(rgba_data))
            elif target is TPCTextureFormat.RGB:
                self.data = rgba_to_rgb(rgba_data)
            elif target is TPCTextureFormat.RGBA:
                self.data = rgba_data
            elif target is TPCTextureFormat.BGRA:
                self.data = rgba_to_bgra(rgba_data)
            elif target is TPCTextureFormat.Greyscale:
                self.data = rgba_to_grey(rgba_data)
            elif target is TPCTextureFormat.DXT1:
                self.data = rgb_to_dxt1(rgba_to_rgb(rgba_data), self.width, self.height)
            elif target is TPCTextureFormat.DXT3:
                self.data = rgba_to_dxt3(rgba_data, self.width, self.height)

        self.tpc_format = target

    def copy(self) -> Self:
        """Create a deep copy of this mipmap."""
        return self.__class__(self.width, self.height, self.tpc_format, self.data.copy())


@dataclass
class TPCLayer(ComparableMixin):
    """A layer in a TPC texture, containing mipmaps."""

    mipmaps: list[TPCMipmap] = field(default_factory=list)
    _layer_width: int = field(default=0, repr=False, compare=False)
    _layer_height: int = field(default=0, repr=False, compare=False)
    _rgba_mipmap_ndix: bool = field(default=False, repr=False, compare=False)

    def set_single(
        self,
        width: int,
        height: int,
        data: bytes | bytearray,
        tpc_format: TPCTextureFormat,
        *,
        rgba_mipmap_ndix: bool = False,
    ):
        """Given a single mipmap, progressively create smaller mipmaps and set them all to the layer."""
        if not isinstance(data, bytearray):
            data = bytearray(data)
        self.mipmaps.clear()
        self._layer_width = width
        self._layer_height = height
        self._rgba_mipmap_ndix = rgba_mipmap_ndix
        mm_width, mm_height = width, height

        while mm_width > 0 and mm_height > 0:
            w, h = max(1, mm_width), max(1, mm_height)
            mm = TPCMipmap(
                width=w,
                height=h,
                tpc_format=tpc_format,
                data=data,
            )
            self.mipmaps.append(mm)

            mm_width >>= 1
            mm_height >>= 1

            # Generate the next mipmap data by downsampling
            if w > 1 and h > 1:
                # RobustLogger().debug(f"Downsampling mipmap ({w}x{h}) to {mm_width}x{mm_height}")
                data = self._downsample(data, w, h, tpc_format)
            else:
                break

            if mm_width < 1 or mm_height < 1:
                break

    def set_data(
        self,
        width: int,
        height: int,
        mipmaps: list[bytes],
        texture_format: TPCTextureFormat,
    ):
        """Sets the new texture data.

        Args:
        ----
            width: The new width.
            height: The new height.
            mipmaps: The new mipmaps data.
            texture_format: The texture format.
        """
        # TODO(NickHugi): Some sort of simple sanity check on the data; make sure the mipmaps' data have the appropriate size

        self.mipmaps.clear()
        mm_width, mm_height = width, height
        for mipmap_data in mipmaps:
            w, h = max(1, mm_width), max(1, mm_height)
            self.mipmaps.append(TPCMipmap(w, h, texture_format, bytearray(mipmap_data)))
            mm_width >>= 1
            mm_height >>= 1

    def _downsample(
        self,
        data: bytearray,
        width: int,
        height: int,
        tpc_format: TPCTextureFormat,
    ) -> bytearray:
        """Downsample the given mipmap data to the next smaller mipmap size."""
        nw, nh = max(1, width // 2), max(1, height // 2)
        if self._rgba_mipmap_ndix and tpc_format == TPCTextureFormat.RGBA:
            from pykotor.resource.formats.tpc.manipulate.mipmap_ndix import downsample_rgba_ndix

            mip_idx = len(self.mipmaps)
            return downsample_rgba_ndix(
                data,
                self._layer_width,
                self._layer_height,
                mip_idx,
                interpolation=True,
            )
        if tpc_format == TPCTextureFormat.DXT1:
            rgb = dxt1_to_rgb(data, width, height)
            small = downsample_rgb(bytearray(rgb), width, height, 3)
            return rgb_to_dxt1(small, nw, nh)
        if tpc_format == TPCTextureFormat.DXT3:
            rgba = dxt3_to_rgba(data, width, height)
            small = downsample_rgb(bytearray(rgba), width, height, 4)
            return rgba_to_dxt3(small, nw, nh)
        if tpc_format == TPCTextureFormat.DXT5:
            rgba = dxt5_to_rgba(data, width, height)
            small = downsample_rgb(bytearray(rgba), width, height, 4)
            return rgba_to_dxt5(small, nw, nh)
        return downsample_rgb(data, width, height, tpc_format.bytes_per_pixel())

    def copy(self) -> Self:
        """Create a deep copy of this layer."""
        return self.__class__([mipmap.copy() for mipmap in self.mipmaps])


class TPC(BiowareResource):
    """BioWare's TPC texture format used in Knights of the Old Republic."""

    BINARY_TYPE = ResourceType.TPC
    BLANK_LAYER = TPCLayer(
        [
            TPCMipmap(
                width,
                height,
                TPCTextureFormat.RGBA,
                bytearray(0 for _ in range(width * height * 4)),
            )
            for width, height in (
                (256, 256),
                (128, 128),
                (64, 64),
                (32, 32),
                (16, 16),
                (8, 8),
                (4, 4),
                (2, 2),
                (1, 1),
            )
        ],
    )

    def __init__(self):
        self._txi: TXI = TXI()
        self._txi_text: str = ""
        self._txi_source_features = None
        self._format: TPCTextureFormat = TPCTextureFormat.Invalid
        self.layers: list[TPCLayer] = []
        self.is_animated: bool = False
        self.is_cube_map: bool = False
        self.alpha_test: float = 1.0

    @classmethod
    def from_blank(cls) -> Self:
        """Create a blank TPC texture."""
        instance = cls()
        instance.layers = [cls.BLANK_LAYER]
        instance._format = TPCTextureFormat.RGBA
        return instance

    @property
    def txi(self) -> str:
        """Get the TXI data as text.

        Preserve the original TXI footer text when this TPC was loaded from a
        source file.  The parsed :class:`TXI` object is still populated for
        feature inspection, animation detection, and compatibility with callers
        that consume ``_txi.features``.
        """
        if (
            self._txi_text.strip()
            and self._txi_source_features is not None
            and vars(self._txi.features) == vars(self._txi_source_features)
        ):
            return self._txi_text
        return str(self._txi)

    @txi.setter
    def txi(self, value: str):
        """Set the TXI data from a string while retaining the source text."""
        self._txi_text = value.split("\x00", maxsplit=1)[0] if value else ""
        self._txi = TXI()
        self._txi_source_features = None
        if self._txi_text.strip():
            self._txi.load(self._txi_text)
            self._txi_source_features = deepcopy(self._txi.features)

    def format(self) -> TPCTextureFormat:
        """Get the texture format."""
        return self._format

    def is_compressed(self) -> bool:
        """Check if the texture is compressed."""
        return self._format in {TPCTextureFormat.DXT1, TPCTextureFormat.DXT3, TPCTextureFormat.DXT5}

    def mipmap_size(self, layer: int, mipmap: int) -> tuple[int, int]:
        """Get the dimensions of a specific mipmap."""
        mm: TPCMipmap = self.layers[layer].mipmaps[mipmap]
        return mm.width, mm.height

    def dimensions(self) -> tuple[int, int]:
        """Get the dimensions of the texture."""
        if not self.layers:
            return 0, 0
        return self.layers[0].mipmaps[0].width, self.layers[0].mipmaps[0].height

    def get(
        self,
        layer: int = 0,
        mipmap: int = 0,
    ) -> TPCMipmap:
        """Get a specific mipmap."""
        return self.layers[layer].mipmaps[mipmap]

    def set_single(
        self, data: bytes | bytearray, tpc_format: TPCTextureFormat, width: int, height: int
    ):
        """Set a single texture layer with the given data."""
        self.layers = [TPCLayer()]
        self.is_cube_map = False
        self.is_animated = False
        self.layers[0].set_single(width, height, data, tpc_format)
        self._format = tpc_format

    def rotate90(self, times: int) -> None:
        """Rotate all mipmaps in 90° steps, clock-wise for positive times, counter-clockwise for negative times."""
        times = times % 4  # Normalize rotation to 0-3
        if times == 0:
            return  # No rotation needed

        for layer in self.layers:
            for mipmap in layer.mipmaps:
                if self._format == TPCTextureFormat.DXT1:
                    mipmap.data = rotate_dxt1(mipmap.data, mipmap.width, mipmap.height, times)
                elif self._format == TPCTextureFormat.DXT5:
                    mipmap.data = rotate_dxt5(mipmap.data, mipmap.width, mipmap.height, times)
                elif not self._format.is_dxt():
                    mipmap.data = rotate_rgb_rgba(
                        mipmap.data,
                        mipmap.width,
                        mipmap.height,
                        self._format.bytes_per_pixel(),
                        times,
                    )
                else:
                    raise ValueError(f"Unsupported format for rotation: {self._format}")

                # Swap width and height for 90° or 270° rotations
                if times % 2 != 0:
                    mipmap.width, mipmap.height = mipmap.height, mipmap.width

    def flip_vertically(self) -> None:
        """Flip all mipmaps vertically."""
        for layer in self.layers:
            for mipmap in layer.mipmaps:
                if self._format.is_dxt():
                    mipmap.data = flip_vertically_dxt(
                        mipmap.data, mipmap.width, mipmap.height, self._format.bytes_per_block()
                    )
                else:
                    mipmap.data = flip_vertically_rgb_rgba(
                        mipmap.data, mipmap.width, mipmap.height, self._format.bytes_per_pixel()
                    )

    def flip_horizontally(self) -> None:
        """Flip all mipmaps horizontally."""
        for layer in self.layers:
            for mipmap in layer.mipmaps:
                if self._format.is_dxt():
                    mipmap.data = flip_horizontally_dxt(
                        mipmap.data, mipmap.width, mipmap.height, self._format.bytes_per_block()
                    )
                else:
                    mipmap.data = flip_horizontally_rgb_rgba(
                        mipmap.data, mipmap.width, mipmap.height, self._format.bytes_per_pixel()
                    )

    def convert(self, target: TPCTextureFormat) -> None:
        """Convert the TPC texture to the specified target format."""
        if self._format == target:
            return

        for layer in self.layers:
            for mipmap in layer.mipmaps:
                mipmap.convert(target)

        self._format = target

    def decode(self):
        """Decode compressed formats to their uncompressed equivalents."""
        if self.format() in (
            TPCTextureFormat.BGR,
            TPCTextureFormat.DXT1,
            TPCTextureFormat.Greyscale,
        ):
            self.convert(TPCTextureFormat.RGB)
        elif self.format() in (TPCTextureFormat.BGRA, TPCTextureFormat.DXT3, TPCTextureFormat.DXT5):
            self.convert(TPCTextureFormat.RGBA)

    def encode(self):
        """Encode uncompressed formats to their compressed equivalents."""
        if self.format() in (
            TPCTextureFormat.RGB,
            TPCTextureFormat.BGR,
            TPCTextureFormat.Greyscale,
        ):
            self.convert(TPCTextureFormat.DXT1)
        elif self.format() in (TPCTextureFormat.RGBA, TPCTextureFormat.BGRA, TPCTextureFormat.DXT5):
            self.convert(TPCTextureFormat.DXT5)

    @staticmethod
    def _integer48(
        bytes48: bytes,
    ) -> int:
        return (
            bytes48[0]
            + (bytes48[1] << 8)
            + (bytes48[2] << 16)
            + (bytes48[3] << 24)
            + (bytes48[4] << 32)
            + (bytes48[5] << 40)
        )

    def copy(self) -> Self:
        """Create a deep copy of this TPC texture."""
        instance: Self = self.__class__.from_blank()
        instance.layers[:] = (layer.copy() for layer in self.layers)
        instance._format = self._format
        instance.is_animated = self.is_animated
        instance.is_cube_map = self.is_cube_map
        instance._txi_text = self._txi_text
        instance._txi_source_features = deepcopy(self._txi_source_features)
        instance._txi = TXI(self.txi)
        return instance
