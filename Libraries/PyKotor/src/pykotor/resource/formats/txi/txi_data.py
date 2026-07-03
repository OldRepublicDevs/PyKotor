"""This module handles TXI (Texture Information) files for KotOR.

TXI files are ASCII text files that provide additional metadata for TPC texture files.
They specify rendering properties (blending modes, mipmaps, filtering), companion textures
(bump maps, environment maps), font metrics for bitmap fonts, and animation parameters for
flipbook textures.

Observed retail behavior:
----------
    KotOR I and TSL treat ``.txi`` sidecars as ASCII key/value scripts that ride alongside
    ``.tpc`` textures—mip bias, blending, companion maps, font metrics, and flipbook timing all
    come from this text.

    Third-party GitHub URL lines removed from this module are archived at
    ``wiki/reverse_engineering_findings_txi_data_github_urls_pre_scrub.md``.
    NWN wiki and module header reference comments are archived at
    ``wiki/reverse_engineering_findings_txi_data_external_refs_pre_scrub.md``.

ASCII Format:
------------
    - TXI files are line-based ASCII text files with command-value pairs:


    Format: <command> <value>
    Example: "mipmap 0"
    Example: "blending additive"
    Example: "upperleftcoords 256"
             0.000000 0.000000 0
             0.031250 0.031250 0
             ...

    Commands are case-insensitive. Values can be integers, floats, booleans (0/1),
    strings (texture names), or multi-line coordinate arrays.

Note:
----
    The TXI class needs to be merged with the TXIBaseInformation and its subclasses
    at some point in an intuitive manner. This is a work in progress.
"""

from __future__ import annotations

import math

from enum import Enum
from typing import ClassVar

from loggerplus import RobustLogger
from pykotor.resource.formats._base import BiowareResource, ComparableMixin


class TXI(BiowareResource):
    def __init__(self, txi: str | None = None):
        self.features: TXIFeatures = TXIFeatures()
        self._empty: bool = True
        if txi is not None:
            self.load(txi)

    def load(self, txi: str):  # noqa: C901, PLR0912, PLR0915
        from pykotor.resource.formats.txi.io_txi import TXIReaderMode

        self.features = TXIFeatures()
        self._empty = True
        txi = txi.replace("\x00", "")
        mode = TXIReaderMode.NORMAL
        cur_coords: int = 0
        max_coords: int = 0
        for line in txi.splitlines():
            try:
                parsed_line: str = line.strip()
                if not parsed_line:
                    continue

                # print(parsed_line)
                if mode == TXIReaderMode.UPPER_LEFT_COORDS:
                    parts: list[str] = parsed_line.split()
                    coords: tuple[float, float, int] = (
                        float(parts[0].strip()),
                        float(parts[1].strip()),
                        int(parts[2].strip()),
                    )
                    if self.features.upperleftcoords is None:
                        self.features.upperleftcoords = []
                    self.features.upperleftcoords.append(coords)
                    cur_coords += 1
                    if cur_coords >= max_coords:
                        mode = TXIReaderMode.NORMAL
                        cur_coords = 0
                    continue

                if mode == TXIReaderMode.LOWER_RIGHT_COORDS:
                    parts = parsed_line.split()
                    coords = (
                        float(parts[0].strip()),
                        float(parts[1].strip()),
                        int(parts[2].strip()),
                    )
                    if self.features.lowerrightcoords is None:
                        self.features.lowerrightcoords = []
                    self.features.lowerrightcoords.append(coords)
                    cur_coords += 1
                    if cur_coords >= max_coords:
                        mode = TXIReaderMode.NORMAL
                    continue

                args: str
                raw_cmd, args = (
                    parsed_line.split(" ", maxsplit=1)
                    if " " in parsed_line
                    else (
                        parsed_line,
                        "",
                    )
                )
                parsed_cmd_str: str = raw_cmd.strip().upper()
                if parsed_cmd_str == "DECAL1":  # per_lt06.tpc, per_lt07.tpc
                    parsed_cmd_str = "DECAL"
                    args = "1"
                if not parsed_cmd_str or parsed_cmd_str not in TXICommand.__members__:
                    RobustLogger().warning(f"Invalid TXI command: '{parsed_line}'")
                    continue
                command: TXICommand = TXICommand.__members__[parsed_cmd_str]
                args = args.strip() if args else ""

                if command == TXICommand.ALPHAMEAN:
                    self.features.alphamean = float(args)
                    self._empty = False
                elif command == TXICommand.ARTUROHEIGHT:
                    self.features.arturoheight = int(args)
                    self._empty = False
                elif command == TXICommand.ARTUROWIDTH:
                    self.features.arturowidth = int(args)
                    self._empty = False
                elif command == TXICommand.BASELINEHEIGHT:
                    self.features.baselineheight = float(args)
                    self._empty = False
                elif command == TXICommand.BLENDING:
                    self.features.blending = self.parse_blending(args)
                    self._empty = False
                elif command == TXICommand.BUMPMAPSCALING:
                    self.features.bumpmapscaling = float(args)
                    self._empty = False
                elif command == TXICommand.BUMPMAPTEXTURE:
                    self.features.bumpmaptexture = args
                    self._empty = False
                elif command == TXICommand.BUMPYSHINYTEXTURE:
                    self.features.bumpyshinytexture = args
                    self._empty = False
                elif command == TXICommand.CANDOWNSAMPLE:
                    self.features.candownsample = bool(int(args))
                    self._empty = False
                elif command == TXICommand.CARETINDENT:
                    self.features.caretindent = float(args)
                    self._empty = False
                elif command == TXICommand.CHANNELSCALE:
                    self.features.channelscale = list(map(float, args.split()))
                    self._empty = False
                elif command == TXICommand.CHANNELTRANSLATE:
                    self.features.channeltranslate = list(map(float, args.split()))
                    self._empty = False
                elif command == TXICommand.CLAMP:
                    self.features.clamp = bool(int(args))
                    self._empty = False
                elif command == TXICommand.CODEPAGE:
                    self.features.codepage = int(args)
                    self._empty = False
                elif command == TXICommand.COLS:
                    self.features.cols = int(args)
                    self._empty = False
                elif command == TXICommand.COMPRESSTEXTURE:
                    self.features.compresstexture = bool(int(args))
                    self._empty = False
                elif command == TXICommand.CONTROLLERSCRIPT:
                    self.features.controllerscript = args
                    self._empty = False
                elif command == TXICommand.CUBE:
                    self.features.cube = bool(int(args)) if args else True
                    self._empty = False
                elif command == TXICommand.DECAL:
                    self.features.decal = bool(int(args))
                    self._empty = False
                elif command == TXICommand.DEFAULTBPP:
                    self.features.defaultbpp = int(args)
                    self._empty = False
                elif command == TXICommand.DEFAULTHEIGHT:
                    self.features.defaultheight = int(args)
                    self._empty = False
                elif command == TXICommand.DEFAULTWIDTH:
                    self.features.defaultwidth = int(args)
                    self._empty = False
                elif command == TXICommand.DISTORT:
                    try:
                        self.features.distort = bool(int(args))
                    except ValueError:
                        self.features.distort = float(args)
                    self._empty = False
                elif command == TXICommand.DISTORTANGLE:
                    self.features.distortangle = float(args)
                    self._empty = False
                elif command == TXICommand.DISTORTIONAMPLITUDE:
                    self.features.distortionamplitude = float(args)
                    self._empty = False
                elif command == TXICommand.DOWNSAMPLEFACTOR:
                    self.features.downsamplefactor = float(args)
                    self._empty = False
                elif command == TXICommand.DOWNSAMPLEMAX:
                    self.features.downsamplemax = int(args)
                    self._empty = False
                elif command == TXICommand.DOWNSAMPLEMIN:
                    self.features.downsamplemin = int(args)
                    self._empty = False
                elif command == TXICommand.ENVMAPTEXTURE:
                    self.features.envmaptexture = args
                    self._empty = False
                elif command == TXICommand.FILERANGE:
                    self.features.filerange = list(map(int, args.split()))
                    self._empty = False
                elif command == TXICommand.FILTER:
                    self.features.filter = bool(int(args))
                    self._empty = False
                elif command == TXICommand.FONTHEIGHT:
                    self.features.fontheight = float(args)
                    self._empty = False
                elif command == TXICommand.FONTWIDTH:
                    self.features.fontwidth = int(args)
                    self._empty = False
                elif command == TXICommand.FPS:
                    self.features.fps = float(args)
                    self._empty = False
                elif command == TXICommand.ISBUMPMAP:
                    self.features.isbumpmap = bool(int(args))
                    self._empty = False
                elif command == TXICommand.ISDIFFUSEBUMPMAP:
                    self.features.isdiffusebumpmap = bool(int(args))
                    self._empty = False
                elif command == TXICommand.ISLIGHTMAP:
                    self.features.islightmap = bool(int(args))
                    self._empty = False
                elif command == TXICommand.ISSPECULARBUMPMAP:
                    self.features.isspecularbumpmap = bool(int(args))
                    self._empty = False
                elif command == TXICommand.LOWERRIGHTCOORDS:
                    if not args:
                        continue
                    cur_coords = 0
                    max_coords = int(args)
                    mode = TXIReaderMode.LOWER_RIGHT_COORDS
                    self._empty = False
                elif command == TXICommand.MAXSIZEHQ:
                    self.features.maxSizeHQ = int(args)
                    self._empty = False
                elif command == TXICommand.MAXSIZELQ:
                    self.features.maxSizeLQ = int(args)
                    self._empty = False
                elif command == TXICommand.MINSIZEHQ:
                    self.features.minSizeHQ = int(args)
                    self._empty = False
                elif command == TXICommand.MINSIZELQ:
                    self.features.minSizeLQ = int(args)
                    self._empty = False
                elif command == TXICommand.MIPMAP:
                    self.features.mipmap = bool(int(args))
                    self._empty = False
                elif command == TXICommand.NUMCHARS:
                    self.features.numchars = int(args)
                    self._empty = False
                elif command == TXICommand.NUMCHARSPERSHEET:
                    self.features.numcharspersheet = int(args)
                    self._empty = False
                elif command == TXICommand.NUMX:
                    self.features.numx = int(args)
                    self._empty = False
                elif command == TXICommand.NUMY:
                    self.features.numy = int(args)
                    self._empty = False
                elif command == TXICommand.ONDEMAND:
                    self.features.ondemand = bool(int(args))
                    self._empty = False
                elif command == TXICommand.PRIORITY:
                    self.features.priority = int(args)
                    self._empty = False
                elif command == TXICommand.PROCEDURETYPE:
                    self.features.proceduretype = args
                    self._empty = False
                elif command == TXICommand.ROWS:
                    self.features.rows = int(args)
                    self._empty = False
                elif command == TXICommand.SPACINGB:
                    self.features.spacingB = float(args)
                    self._empty = False
                elif command == TXICommand.SPACINGR:
                    self.features.spacingR = float(args)
                    self._empty = False
                elif command == TXICommand.SPEED:
                    self.features.speed = float(args)
                    self._empty = False
                elif command == TXICommand.TEMPORARY:
                    self.features.temporary = bool(int(args))
                    self._empty = False
                elif command == TXICommand.TEXTUREWIDTH:
                    self.features.texturewidth = float(args)
                    self._empty = False
                elif command == TXICommand.UNIQUE:
                    self.features.unique = bool(int(args))
                    self._empty = False
                elif command == TXICommand.UPPERLEFTCOORDS:
                    if not args:
                        continue
                    cur_coords = 0
                    max_coords = int(args)
                    mode = TXIReaderMode.UPPER_LEFT_COORDS
                    self._empty = False
                elif command == TXICommand.WATERALPHA:
                    self.features.wateralpha = float(args)
                    self._empty = False
                elif command == TXICommand.WATERHEIGHT:
                    self.features.waterheight = float(args)
                    self._empty = False
                elif command == TXICommand.WATERWIDTH:
                    self.features.waterwidth = float(args)
                    self._empty = False
                elif command == TXICommand.XBOX_DOWNSAMPLE:
                    self.features.xbox_downsample = bool(int(args))
                    self._empty = False
            except Exception as e:  # noqa: BLE001
                RobustLogger().warning(f"Invalid TXI line: '{line}'", exc_info=e)

    def empty(self) -> bool:
        return self._empty

    def get_features(self) -> TXIFeatures:
        return self.features

    @staticmethod
    def parse_blending(s: str) -> int:
        s_norm = (s or "").strip().lower()
        if s_norm in {"additive", "1"}:
            return 1
        if s_norm in {"punchthrough", "punch-through", "2"}:
            return 2
        return 0

    def __str__(self) -> str:
        lines: list[str] = []
        for attr, value in vars(self.features).items():
            if value is None or attr.startswith("__"):
                continue
            upper_attr = attr.upper()
            if upper_attr not in TXICommand.__members__:
                RobustLogger().error(f"Invalid TXI attribute '{attr}'")
                continue
            command: TXICommand = TXICommand[upper_attr]
            if command == TXICommand.BLENDING and isinstance(value, int):
                lines.append(f"{command.value} {self._stringify_blending(value)}")
            elif isinstance(value, bool):
                lines.append(f"{command.value} {int(value)}")
            elif isinstance(value, (int, float)):
                lines.append(f"{command.value} {value}")
            elif isinstance(value, list):
                if attr in [TXICommand.UPPERLEFTCOORDS.value, TXICommand.LOWERRIGHTCOORDS.value]:
                    lines.append(command.value)
                    lines.extend(" ".join(map(str, coord)) for coord in value)
                else:
                    lines.append(f"{command.value} {' '.join(map(str, value))}")
            else:
                lines.append(f"{command.value} {value}")
        return "\n".join(lines)

    @staticmethod
    def _stringify_blending(value: int) -> str:
        return {1: "additive", 2: "punchthrough"}.get(value, str(value))


class TXIFeatures(ComparableMixin):
    """Stores texture features parsed from TXI file.

    TXIFeatures contains all properties that can be specified in a TXI file, including
    rendering properties (blending, mipmaps, filtering), companion textures (bump maps,
    environment maps), font metrics for bitmap fonts, and animation parameters.

    Attributes:
    ----------
        blending: Blending mode for texture rendering (0=None, 1=Additive, 2=PunchThrough)
            Controls how texture blends with background (additive for glowing effects, punchthrough for transparency)

        mipmap: Enable mipmap generation (0=disabled, 1=enabled)
            Reference: PyKotor txi_data.py:396 (mipmap comment)
            NOTE: Engine has broken mip implementation - incorrectly mixes mip levels even on full-screen objects
            Setting to 0 tells engine to use highest resolution (mip 0)

        filter: Enable texture filtering (0=nearest, 1=linear)
            Applies graphical "softening" on fonts (doesn't affect spacing)
            NOTE: Broken implementation in engine, avoid using

        decal: Enable decal rendering mode (0=disabled, 1=enabled)
            Decals are rendered on top of geometry without affecting depth buffer

        cube: Enable cube map texture (0=disabled, 1=enabled)
            Cube maps are used for environment mapping (skyboxes, reflections)

        bumpmaptexture: ResRef of bump map texture companion
            Companion texture providing normal map data for bump mapping

        bumpyshinytexture: ResRef of bumpy shiny texture companion
            Companion texture combining bump and specular mapping

        envmaptexture: ResRef of environment map texture companion
            Companion texture for environment mapping (reflections)

        bumpmapscaling: Scaling factor for bump map intensity
            Controls how pronounced bump mapping effects are (default 1.0)

        wateralpha: Alpha transparency for water textures (0.0-1.0)
            Used with proceduretype "water" for water surface rendering

        proceduretype: Animation procedure type ("cycle", "water", "arturo", etc.)
            "cycle" = flipbook animation, "water" = water shader, "arturo" = unknown effect

        numx: Number of frames horizontally in flipbook animation
            Used with proceduretype "cycle" for flipbook textures

        numy: Number of frames vertically in flipbook animation
            Used with proceduretype "cycle" for flipbook textures

        fps: Frames per second for flipbook animation
            Animation speed for flipbook textures (proceduretype "cycle")

        numchars: Number of characters in font texture
            NOTE: Unsure if required - game may derive from upperleftcoords/lowerrightcoords sizes

        fontheight: Font height in normalized coordinates (0.0-1.0)
            Height of font characters in texture space (normalized 0-1)

        baselineheight: Baseline height for font rendering (0.0-1.0)
            Vertical position of text baseline in normalized coordinates
            Untested - may control accent positioning above characters

        texturewidth: Texture width scaling factor for fonts
            Actual displayed width of texture, allows stretching/compressing along X axis
            Tested - controls font width scaling

        spacingR: Horizontal spacing between characters (0.0-1.0)
            NOTE: Should NEVER exceed maximum of 0.002600 according to research
            Untested - controls character spacing horizontally

        spacingB: Vertical spacing between lines (0.0-1.0)
            Confirmed - spacing between each multiline string rendered in-game
            Float between 0 and 1

        caretindent: Indent for caret/accent marks above characters
            Probably determines accent information above character
            Probably negative since Y is inverted (default -0.010000)
            Untested

        upperleftcoords: List of upper-left UV coordinates for font character boxes
            Each tuple: (x, y, z) where x,y are normalized 0-1, z is always 0
            Confirmed - top-left coordinates for character boxes game draws

        lowerrightcoords: List of lower-right UV coordinates for font character boxes
            Each tuple: (x, y, z) where x,y are normalized 0-1, z is always 0
            Confirmed - bottom-right coordinates for character boxes game draws

        isbumpmap: Flag indicating texture is a bump map (0=no, 1=yes)
            Marks texture as normal map for bump mapping

        islightmap: Flag indicating texture is a lightmap (0=no, 1=yes)
            Marks texture as pre-baked lighting data

        downsamplemin: Minimum downsample level
            Probably unsupported or broken related to mipmap issues

        downsamplemax: Maximum downsample level
            Probably unsupported or broken related to mipmap issues

        defaultwidth: Default texture width (pixels)
            Default width hint for texture loading

        defaultheight: Default texture height (pixels)
            Default height hint for texture loading

        compresstexture: Enable texture compression (0=no, 1=yes)
            Controls whether texture should be compressed in memory

        clamp: Enable texture clamping (0=repeat, 1=clamp)
            Controls texture wrapping behavior at edges

        alphamean: Mean alpha value for alpha testing
            Reference: PyKotor txi_data.py:335 (alphamean field)
            Used for alpha testing optimization

        filter: Texture filtering mode (separate from mipmap filter)
            Reference: PyKotor txi_data.py:371 (filter comment)
            NOTE: Broken implementation in engine

        Other fields (arturoheight, arturowidth, channelscale, channeltranslate, codepage,
        cols, controllerscript, dbmapping, defaultbpp, distort, distortangle,
        distortionamplitude, downsamplefactor, filerange, isdiffusebumpmap, isdoublebyte,
        isspecularbumpmap, maxSizeHQ, maxSizeLQ, minSizeHQ, minSizeLQ, numcharspersheet,
        ondemand, priority, rows, speed, temporary, unique, waterheight, waterwidth,
        xbox_downsample): Additional TXI commands with varying support levels
            Some are NWN-specific, some are KotOR-specific, some are unimplemented
    """

    def __init__(self):  # noqa: PLR0915
        # Blending mode (0=None, 1=Additive, 2=PunchThrough)
        self.blending: int | None = None

        # Enable mipmap generation (0=disabled, 1=enabled, NOTE: broken in engine)
        self.mipmap: bool | None = None

        # Enable texture filtering (NOTE: broken implementation)
        self.filter: bool | None = None

        # Enable decal rendering mode
        self.decal: bool | None = None

        # Enable cube map texture
        self.cube: bool | None = None

        # ResRef of bump map texture companion
        self.bumpmaptexture: str | None = None

        # ResRef of bumpy shiny texture companion
        self.bumpyshinytexture: str | None = None

        # ResRef of environment map texture companion
        self.envmaptexture: str | None = None

        # Scaling factor for bump map intensity
        self.bumpmapscaling: float | None = None

        # Alpha transparency for water textures (0.0-1.0)
        self.wateralpha: float | None = None

        # Animation procedure type ("cycle", "water", "arturo", etc.)
        self.proceduretype: str | None = None

        # Number of frames horizontally in flipbook animation
        self.numx: int | None = None

        # Number of frames vertically in flipbook animation
        self.numy: int | None = None

        # Frames per second for flipbook animation
        self.fps: float | None = None

        # Number of characters in font texture (may be derived from coords)
        self.numchars: int | None = None

        # Font height in normalized coordinates (0.0-1.0)
        self.fontheight: float | None = None

        # Font width in normalized coordinates (0.0-1.0)
        self.fontwidth: float | None = None

        # Baseline height for font rendering (0.0-1.0)
        self.baselineheight: float | None = None

        # Texture width scaling factor for fonts
        self.texturewidth: float | None = None

        # Horizontal spacing between characters (0.0-1.0, max 0.002600)
        self.spacingR: float | None = None

        # Vertical spacing between lines (0.0-1.0)
        self.spacingB: float | None = None

        # Indent for caret/accent marks above characters (probably negative)
        self.caretindent: float | None = None

        # Upper-left UV coordinates for font character boxes (normalized 0-1, z always 0)
        self.upperleftcoords: list[tuple[float, float, int]] | None = None

        # Lower-right UV coordinates for font character boxes (normalized 0-1, z always 0)
        self.lowerrightcoords: list[tuple[float, float, int]] | None = None

        # Additional fields (many are NWN-specific or unimplemented)
        self.alphamean: float | None = None
        self.arturoheight: int | None = None
        self.arturowidth: int | None = None
        self.candownsample: bool | None = None
        self.channelscale: list[float] | None = None
        self.channeltranslate: list[float] | None = None
        self.clamp: bool | None = None
        self.codepage: int | None = None
        self.cols: int | None = None
        self.compresstexture: bool | None = None
        self.controllerscript: str | None = None
        self.defaultbpp: int | None = None
        self.defaultheight: int | None = None
        self.defaultwidth: int | None = None
        self.distort: bool | float | None = None
        self.distortangle: float | None = None
        self.distortionamplitude: float | None = None
        self.downsamplefactor: float | None = None
        self.downsamplemax: int | None = None
        self.downsamplemin: int | None = None
        self.filerange: list[int] | None = None
        self.isbumpmap: bool | None = None
        self.isdiffusebumpmap: bool | None = None
        self.islightmap: bool | None = None
        self.isspecularbumpmap: bool | None = None
        self.maxSizeHQ: int | None = None
        self.maxSizeLQ: int | None = None
        self.minSizeHQ: int | None = None
        self.minSizeLQ: int | None = None
        self.numcharspersheet: int | None = None
        self.ondemand: bool | None = None
        self.priority: int | None = None
        self.rows: int | None = None
        self.speed: float | None = None
        self.temporary: bool | None = None
        self.unique: bool | None = None
        self.waterheight: float | None = None
        self.waterwidth: float | None = None
        self.xbox_downsample: bool | None = None

    @property
    def is_flipbook(self) -> bool:
        """Return True when the TXI describes a flipbook animation."""
        return (
            isinstance(self.proceduretype, str)
            and self.proceduretype.lower() == "cycle"
            and bool(self.numx)
            and bool(self.numy)
            and bool(self.fps)
        )


class TXICommand(Enum):
    """This class is used to store the commands of a texture."""

    ALPHAMEAN = "alphamean"
    ARTUROHEIGHT = "arturoheight"
    ARTUROWIDTH = "arturowidth"
    BASELINEHEIGHT = "baselineheight"
    BLENDING = "blending"
    BUMPMAPSCALING = "bumpmapscaling"
    BUMPMAPTEXTURE = "bumpmaptexture"
    BUMPYSHINYTEXTURE = "bumpyshinytexture"
    CANDOWNSAMPLE = "candownsample"
    CARETINDENT = "caretindent"
    CHANNELSCALE = "channelscale"
    CHANNELTRANSLATE = "channeltranslate"
    CLAMP = "clamp"
    CODEPAGE = "codepage"
    COLS = "cols"
    COMPRESSTEXTURE = "compresstexture"
    CONTROLLERSCRIPT = "controllerscript"
    CUBE = "cube"
    DECAL = "decal"
    DEFAULTBPP = "defaultbpp"
    DEFAULTHEIGHT = "defaultheight"
    DEFAULTWIDTH = "defaultwidth"
    DISTORT = "distort"
    DISTORTANGLE = "distortangle"
    DISTORTIONAMPLITUDE = "distortionamplitude"
    DOWNSAMPLEFACTOR = "downsamplefactor"
    DOWNSAMPLEMAX = "downsamplemax"
    DOWNSAMPLEMIN = "downsamplemin"
    ENVMAPTEXTURE = "envmaptexture"
    FILERANGE = "filerange"
    FILTER = "filter"
    FONTHEIGHT = "fontheight"
    FONTWIDTH = "fontwidth"
    FPS = "fps"
    ISBUMPMAP = "isbumpmap"
    ISDIFFUSEBUMPMAP = "isdiffusebumpmap"
    ISLIGHTMAP = "islightmap"
    ISSPECULARBUMPMAP = "isspecularbumpmap"
    LOWERRIGHTCOORDS = "lowerrightcoords"
    MAXSIZEHQ = "maxSizeHQ"
    MAXSIZELQ = "maxSizeLQ"
    MINSIZEHQ = "minSizeHQ"
    MINSIZELQ = "minSizeLQ"
    MIPMAP = "mipmap"
    NUMCHARS = "numchars"
    NUMCHARSPERSHEET = "numcharspersheet"
    NUMX = "numx"
    NUMY = "numy"
    ONDEMAND = "ondemand"
    PRIORITY = "priority"
    PROCEDURETYPE = "proceduretype"
    ROWS = "rows"
    SPACINGB = "spacingB"
    SPACINGR = "spacingR"
    SPEED = "speed"
    TEMPORARY = "temporary"
    TEXTUREWIDTH = "texturewidth"
    UNIQUE = "unique"
    UPPERLEFTCOORDS = "upperleftcoords"
    WATERALPHA = "wateralpha"
    WATERHEIGHT = "waterheight"
    WATERWIDTH = "waterwidth"
    XBOX_DOWNSAMPLE = "xbox_downsample"


class TXIBaseInformation(ComparableMixin):
    COMPARABLE_FIELDS = (
        "downsamplemax",
        "downsamplemin",
        "filter",
        "mipmap",
    )
    """Fields used within all txi files."""

    def __init__(self):
        # Mipmap and Filter settings (0/1) can apply different graphical "softening" on the fonts (not affecting
        # spacing etc.). Don't use it though, in most case it would hurt your eyes.
        # The engine has broken mip use implementation. It incorrectly mixes mip levels, even on objects
        # filling the screen.
        self.mipmap: int = 0  # The mipmap 0 setting shouldn't be changed. That tells the engine to use mip 0, i.e. the
        # highest resolution of the image
        self.filter: int = 0  # (???)
        self.downsamplemin: int = 0  # (???) (probably unsupported or broken related to above)
        self.downsamplemax: int = 0  # (???) (probably unsupported or broken related to above)

    def __eq__(self, other):
        if not isinstance(other, TXIBaseInformation):
            return NotImplemented  # type: ignore[no-any-return]
        return (
            self.mipmap == other.mipmap
            and self.filter == other.filter
            and self.downsamplemin == other.downsamplemin
            and self.downsamplemax == other.downsamplemax
        )

    def __hash__(self):
        return hash((self.mipmap, self.filter, self.downsamplemin, self.downsamplemax))


class TXIMaterialInformation(TXIBaseInformation):
    COMPARABLE_FIELDS = (
        *TXIBaseInformation.COMPARABLE_FIELDS,
        "blending",
        "bumpmaptexture",
        "bumpreplacementtexture",
        "bumpyshinytexture",
        "decal",
        "envmaptexture",
    )

    def __init__(self):
        super().__init__()
        self.bumpmaptexture: int = 0
        self.bumpyshinytexture: int = 0
        self.envmaptexture: int = 0
        self.bumpreplacementtexture: int = 0
        self.blending: int = 0
        self.decal: int = 0

    def __eq__(self, other):
        if not isinstance(other, TXIMaterialInformation):
            return NotImplemented  # type: ignore[no-any-return]
        return (
            super().__eq__(other)
            and self.bumpmaptexture == other.bumpmaptexture
            and self.bumpyshinytexture == other.bumpyshinytexture
            and self.envmaptexture == other.envmaptexture
            and self.bumpreplacementtexture == other.bumpreplacementtexture
            and self.blending == other.blending
            and self.decal == other.decal
        )

    def __hash__(self):
        return hash(
            (
                super().__hash__(),
                self.bumpmaptexture,
                self.bumpyshinytexture,
                self.envmaptexture,
                self.bumpreplacementtexture,
                self.blending,
                self.decal,
            )
        )


class TXITextureInformation(TXIBaseInformation):
    COMPARABLE_FIELDS = (
        *TXIBaseInformation.COMPARABLE_FIELDS,
        "alphamean",
        "bumpintensity",
        "bumpmapscaling",
        "clamp",
        "cube",
        "defaultheight",
        "defaultwidth",
        "filerange",
        "filter",
        "gamma",
        "isbumpmap",
        "isdiffusebumpmap",
        "isenvironmentmapped",
        "isspecularbumpmap",
        "maptexelstopixels",
        "numx",
        "numy",
        "pltreplacement",
        "proceduretype",
        "specularcolor",
        "temporary",
        "useglobalalpha",
    )

    def __init__(self):
        super().__init__()
        self.proceduretype: int = 0
        self.filerange: int = 0
        self.defaultwidth: int = 0
        self.defaultheight: int = 0
        self.filter: int = 0
        self.maptexelstopixels: int = 0
        self.gamma: int = 0
        self.isbumpmap: int = 0
        self.clamp: int = 0
        self.alphamean: int = 0
        self.isdiffusebumpmap: int = 0
        self.isspecularbumpmap: int = 0
        self.bumpmapscaling: int = 0
        self.specularcolor: int = 0
        self.numx: int = 0
        self.numy: int = 0
        self.cube: int = 0
        self.bumpintensity: int = 0
        self.temporary: int = 0
        self.useglobalalpha: int = 0
        self.isenvironmentmapped: int = 0
        self.pltreplacement: int = 0

    def __eq__(self, other):
        if not isinstance(other, TXITextureInformation):
            return NotImplemented  # type: ignore[no-any-return]
        return (
            super().__eq__(other)
            and self.proceduretype == other.proceduretype
            and self.filerange == other.filerange
            and self.defaultwidth == other.defaultwidth
            and self.defaultheight == other.defaultheight
            and self.filter == other.filter
            and self.maptexelstopixels == other.maptexelstopixels
            and self.gamma == other.gamma
            and self.isbumpmap == other.isbumpmap
            and self.clamp == other.clamp
            and self.alphamean == other.alphamean
            and self.isdiffusebumpmap == other.isdiffusebumpmap
            and self.isspecularbumpmap == other.isspecularbumpmap
            and self.bumpmapscaling == other.bumpmapscaling
            and self.specularcolor == other.specularcolor
            and self.numx == other.numx
            and self.numy == other.numy
            and self.cube == other.cube
            and self.bumpintensity == other.bumpintensity
            and self.temporary == other.temporary
            and self.useglobalalpha == other.useglobalalpha
            and self.isenvironmentmapped == other.isenvironmentmapped
            and self.pltreplacement == other.pltreplacement
        )

    def __hash__(self):
        return hash(
            (
                super().__hash__(),
                self.proceduretype,
                self.filerange,
                self.defaultwidth,
                self.defaultheight,
                self.filter,
                self.maptexelstopixels,
                self.gamma,
                self.isbumpmap,
                self.clamp,
                self.alphamean,
                self.isdiffusebumpmap,
                self.isspecularbumpmap,
                self.bumpmapscaling,
                self.specularcolor,
                self.numx,
                self.numy,
                self.cube,
                self.bumpintensity,
                self.temporary,
                self.useglobalalpha,
                self.isenvironmentmapped,
                self.pltreplacement,
            ),
        )


class TXIFontInformation(TXIBaseInformation):
    DEFAULT_RESOLUTION: ClassVar[int] = 512
    FONT_TEXTURES: ClassVar[
        list[str]
    ] = [  # TODO(th3w1zard1): figure out which ones the game actually uses.
        "fnt_galahad14",  # Main menu?
        "dialogfont10x10",
        "dialogfont10x10a",
        "dialogfont10x10b",
        "dialogfont12x16",
        "dialogfont16x16",
        "dialogfont16x16a",
        "dialogfont16x16a",
        "dialogfont16x16b",
        "dialogfont32x32",
        "fnt_console",  # 127 chars, also has a large horizontally-wide rectangle probably used as the consolebox display
        "fnt_credits",
        "fnt_creditsa",
        "fnt_creditsb",
        "fnt_d10x10b",
        "fnt_d16x16",
        "fnt_d16x16a",
        "fnt_d16x16b",
        "fnt_dialog16x16",
    ]

    TSL_ADDED_FONTS: ClassVar[list[str]] = [
        "dialogfont16x16b",
        "d2xfnt_d16x16b",
        "d2xfont16x16b",
        "d2xfont16x16b_ps",
        "d3xfnt_d16x16b",
        "d3xfont16x16b",
        "d3xfont16x16b_ps",
        "diafnt16x16b_ps",
    ]

    ASPYR_CONTROLLER_BUTTON_TEXTURES: ClassVar[list[str]] = [
        "cus_button_a",
        "cus_button_aps",
        "cus_button_b",
        "cus_button_bps",
        "cus_button_x",
        "cus_button_xps",
        "cus_button_y",
        "cus_button_yps",
    ]

    def __init__(self):
        super().__init__()
        # Actual fields
        self.numchars: int = 256  # Tested. Unsure if this is actually required, or if the game simply takes from the 'upperleftcoords' and 'lowerrightcoords' sizes.
        self.spacingR: float = 0  # Untested. Float between 0 and 1. According to research, should NEVER exceed the maximum of 0.002600
        self.spacingB: float = 0  # Confirmed. Float between 0 and 1. Spacing between each multiline string rendered ingame.
        # Untested. Probably determines the accent information above the character. Probably negative since Y is inverted so this checks out.
        self.caretindent: float = -0.010000
        # Tested. Float between 0 and 1. Was told this actually stretches text down somehow. But in k1 tests, changing this does not yield any noticeable ingame result.  # noqa: E501
        self.fontwidth: float = 1.000000
        # Initialize to safe defaults so __str__ and hashing work before metrics are set.
        self.fontheight: float = 0.0  # Tested. Float between 0 and 1.
        self.baselineheight: float = 0.0  # Untested. Float between 0 and 1.
        self.texturewidth: float = 0.0  # Tested. Float between 0 and 1. Actual displayed width of the texture, allows stretching/compressing along the X axis.

        self.upper_left_coords: list[
            tuple[float, float, int]
        ] = []  # Confirmed. The top left coordinates for the character box the game draws. each float is 0 to 1. 3rd tuple int is always 0  # noqa: E501
        self.lower_right_coords: list[
            tuple[float, float, int]
        ] = []  # Confirmed. The bottom right coordinates for the character box the game draws. each float is 0 to 1. 3rd tuple int is always 0  # noqa: E501

    COMPARABLE_FIELDS = (
        *TXIBaseInformation.COMPARABLE_FIELDS,
        "caretindent",
        "fontwidth",
        "numchars",
        "spacingB",
        "spacingR",
    )
    COMPARABLE_SEQUENCE_FIELDS = ("upper_left_coords", "lower_right_coords")

    def __hash__(self):
        return hash(
            (
                super().__hash__(),
                self.numchars,
                self.spacingR,
                self.spacingB,
                self.caretindent,
                self.fontwidth,
                self.fontheight,
                self.baselineheight,
                self.texturewidth,
                tuple(self.upper_left_coords),
                tuple(self.lower_right_coords),
            ),
        )
        #
        # The 3rd int in the upperleftcoords and bottomright coords is unknown. It could be any of the following:
        #
        # Layer or Depth Information: In some graphic systems, a third coordinate can be used to represent the depth or
        # layer, especially in 3D rendering contexts. However, for 2D font rendering, this is less
        # likely unless there is some form of layering or depth effect involved.
        #
        # Reserved for Future Use or Extension: It's common in software development to include elements
        # in data structures that are reserved for potential future use. This allows for extending the
        # functionality without breaking existing formats or compatibility.
        #
        # Indicator or Flag: This integer could serve as a flag or an indicator for specific conditions or states.
        #
        # Alignment or Padding: In some data structures, additional elements
        # are included for alignment purposes, ensuring that the data aligns well with memory boundaries

    def __str__(self):
        # Format the coordinates (left 4 spaces are not required, but make formatting cleaner)
        ul_coords_str = "\n".join(
            [f"    {x:.6f} {y:.6f} {not_z}" for x, y, not_z in self.upper_left_coords]
        )
        lr_coords_str = "\n".join(
            [f"    {x:.6f} {y:.6f} {not_z}" for x, y, not_z in self.lower_right_coords]
        )

        return f"""
mipmap {self.mipmap}
filter {self.filter}
numchars {self.numchars}
fontheight {self.fontheight:.6f}
baselineheight {self.baselineheight:.6f}
texturewidth {self.texturewidth:.6f}
fontwidth {self.fontwidth:.6f}
spacingR {self.spacingR:.6f}
spacingB {self.spacingB:.6f}
caretindent {self.caretindent:.6f}
upperleftcoords {self.ul_coords_count}
{ul_coords_str}
lowerrightcoords {self.lr_coords_count}
{lr_coords_str}
"""

    @property
    def ul_coords_count(self) -> int:
        return len(self.upper_left_coords)

    @property
    def lr_coords_count(self) -> int:
        return len(self.upper_left_coords)

    def get_scaling_factor(self) -> float:
        return 2 ** (math.log2(self.DEFAULT_RESOLUTION) - 1)

    def coords_from_normalized(
        self,
        upper_left_coords: list[tuple[float, float, int]],
        lower_right_coords: list[tuple[float, float, int]],
        resolution: tuple[int, int],
    ) -> list[tuple[int, int, int, int]]:
        """Converts normalized coordinates to bounding boxes.

        Args:
        ----
            upper_left_coords: List of tuples containing normalized upper left coordinates of boxes
            lower_right_coords: List of tuples containing normalized lower right coordinates of boxes
            resolution: Tuple containing image width and height

        Returns:
        -------
            boxes: List of bounding boxes as lists of [x1,y1,x2,y2] coordinates
        """
        boxes: list[tuple[int, int, int, int]] = []
        for (ulx, uly, _), (lrx, lry, _) in zip(upper_left_coords, lower_right_coords):
            # Convert normalized coords back to pixel coords
            pixel_ulx = ulx * resolution[0]
            pixel_uly = (1 - uly) * resolution[1]  # Y is inverted
            pixel_lrx = lrx * resolution[0]
            pixel_lry = (1 - lry) * resolution[1]  # Y is inverted
            boxes.append((int(pixel_ulx), int(pixel_uly), int(pixel_lrx), int(pixel_lry)))
        return boxes

    def normalize_coords(
        self,
        boxes: list[tuple[float, float, float, float]],
        resolution: tuple[int, int],
    ) -> tuple[list[tuple[float, float, int]], list[tuple[float, float, int]]]:
        """Converts boxes to normalized coordinates.

        Args:
        ----
            boxes: list of bounding boxes as tuples of floats
            resolution: tuple of ints specifying image width and height

        Returns:
        -------
            tuple: tuple containing lists of normalized upper left and lower right box coordinates
        """
        upper_left_coords: list[tuple[float, float, int]] = []
        lower_right_coords: list[tuple[float, float, int]] = []

        for box in boxes:
            ulx, uly, lrx, lry = box
            # Convert pixel coords to normalized coords
            norm_ulx = ulx / resolution[0]
            norm_uly = 1 - (uly / resolution[1])  # Y is inverted
            norm_lrx = lrx / resolution[0]
            norm_lry = 1 - (lry / resolution[1])  # Y is inverted
            upper_left_coords.append((norm_ulx, norm_uly, 0))
            lower_right_coords.append((norm_lrx, norm_lry, 0))

        return upper_left_coords, lower_right_coords

    def set_font_metrics(
        self,
        resolution: tuple[int, int],
        max_char_height: float,
        baseline_height: float,
        custom_scaling: float,
    ):
        self.texturewidth = 128 * custom_scaling / 25
        self.fontheight = 128 * custom_scaling * max_char_height / (25 * resolution[1])
        self.baselineheight = baseline_height / resolution[1]

        # self.texturewidth: float = self.numchars * custom_scaling / 50  # maybe?
        # self.fontheight: float = (self.numchars * custom_scaling * max_char_height) / (50 * resolution[1])  # maybe?

        # TODO(th3w1zard1): I'm pretty sure fontwidth could be calculated here too.
        # During testing it's been easier to leave that at 1.000000 so there's fewer variables to worry about.
        # We should figure out the relationship for proper readability. I think vanilla K1 defines texturewidth as 'resolution_x / 100'.
        # Also worth mentioning the above math doesn't even work if the resolution isn't a perfect square.
        # EDIT: Editing fontwidth yields no changes in K1. Might do something in K2.
        # self.fontwidth = self.texturewidth / self.fontheight * max_char_height / resolution[0]
        # assert int(round(self.fontwidth)) == 1
