"""TXI (texture info) binary reader/writer and coordinate modes."""

from __future__ import annotations

from enum import IntEnum
from typing import TYPE_CHECKING

import kaitaistruct

from bioware_kaitai_formats.txi import Txi

from loggerplus import RobustLogger
from pykotor.resource.type import ResourceReader, ResourceWriter, autoclose

if TYPE_CHECKING:
    from pykotor.resource.formats.txi.txi_data import TXI, TXICommand
    from pykotor.resource.type import SOURCE_TYPES, TARGET_TYPES


class TXIReaderMode(IntEnum):
    """This class is used to store the modes of a texture."""

    NORMAL = 0
    UPPER_LEFT_COORDS = 1
    LOWER_RIGHT_COORDS = 2


class TXIBinaryReader(ResourceReader):
    """Reads TXI (Texture Information) files.

    TXI files contain texture metadata including blending modes, bump maps, animations,
    and other rendering properties for TPC textures.

    References:
    ----------
        Retail builds parse TXI text through the same Aurora texture-info path as other Odyssey
        titles.

    """

    def __init__(self, source: SOURCE_TYPES, offset: int = 0, size: int = 0):
        super().__init__(source, offset, size)
        from pykotor.resource.formats.txi.txi_data import TXI

        self._txi: TXI = TXI()

    @autoclose
    def load(self, *func_args, auto_close: bool = True, **func_kwargs) -> TXI:  # noqa: FBT001, FBT002, ARG002
        from pykotor.resource.formats.txi.txi_data import TXI, TXICommand, TXIFeatures

        self._txi.features = TXIFeatures()
        self._empty: bool = True
        mode: TXIReaderMode = TXIReaderMode.NORMAL
        cur_coords: int = 0
        txi_bytes: bytes = self._reader.read_all()
        try:
            txi_text = Txi.from_bytes(txi_bytes).content
        except (kaitaistruct.KaitaiStructError, UnicodeDecodeError):
            txi_text = txi_bytes.decode("ascii", errors="ignore")
        txi_text = txi_text.replace("\x00", "")

        for line in txi_text.splitlines():
            try:
                parsed_line: str = line.strip()
                if not parsed_line:
                    continue

                # Check if this line is a command (starts with a known TXI command)
                # This allows commands to interrupt coordinate parsing
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
                is_command: bool = parsed_cmd_str in TXICommand.__members__

                if mode == TXIReaderMode.UPPER_LEFT_COORDS:
                    if is_command:
                        # Command encountered, exit coordinate mode and process the command
                        mode = TXIReaderMode.NORMAL
                    else:
                        # Try to parse as coordinates
                        try:
                            parts = parsed_line.split()
                            coords: tuple[float, float, int] = (
                                float(parts[0].strip()),
                                float(parts[1].strip()),
                                int(parts[2].strip()),
                            )
                            if self._txi.features.upperleftcoords is not None and cur_coords < len(
                                self._txi.features.upperleftcoords
                            ):
                                self._txi.features.upperleftcoords[cur_coords] = coords
                            cur_coords += 1
                            if self._txi.features.upperleftcoords is not None and cur_coords >= len(
                                self._txi.features.upperleftcoords
                            ):
                                mode = TXIReaderMode.NORMAL
                            continue
                        except (ValueError, IndexError):
                            # Not a valid coordinate line, might be a command, exit coordinate mode
                            mode = TXIReaderMode.NORMAL
                            # Fall through to process as command or skip

                if mode == TXIReaderMode.LOWER_RIGHT_COORDS:
                    if is_command:
                        # Command encountered, exit coordinate mode and process the command
                        mode = TXIReaderMode.NORMAL
                    else:
                        # Try to parse as coordinates
                        try:
                            parts = parsed_line.split()
                            coords = (
                                float(parts[0].strip()),
                                float(parts[1].strip()),
                                int(parts[2].strip()),
                            )
                            if self._txi.features.lowerrightcoords is not None and cur_coords < len(
                                self._txi.features.lowerrightcoords
                            ):
                                self._txi.features.lowerrightcoords[cur_coords] = coords
                            cur_coords += 1
                            if (
                                self._txi.features.lowerrightcoords is not None
                                and cur_coords >= len(self._txi.features.lowerrightcoords)
                            ):
                                mode = TXIReaderMode.NORMAL
                            continue
                        except (ValueError, IndexError):
                            # Not a valid coordinate line, might be a command, exit coordinate mode
                            mode = TXIReaderMode.NORMAL
                            # Fall through to process as command or skip

                if not is_command:
                    RobustLogger().debug(f"Invalid TXI command: '{raw_cmd}'")
                    continue
                command: TXICommand = TXICommand.__members__[parsed_cmd_str]
                args = args.strip() if args else ""

                if command == TXICommand.ALPHAMEAN:
                    self._txi.features.alphamean = float(args)
                    self._empty = False
                elif command == TXICommand.ARTUROHEIGHT:
                    self._txi.features.arturoheight = int(args)
                    self._empty = False
                elif command == TXICommand.ARTUROWIDTH:
                    self._txi.features.arturowidth = int(args)
                    self._empty = False
                elif command == TXICommand.BASELINEHEIGHT:
                    self._txi.features.baselineheight = float(args)
                    self._empty = False
                elif command == TXICommand.BLENDING:
                    self._txi.features.blending = TXI.parse_blending(args)
                    self._empty = False
                elif command == TXICommand.BUMPMAPSCALING:
                    self._txi.features.bumpmapscaling = float(args)
                    self._empty = False
                elif command == TXICommand.BUMPMAPTEXTURE:
                    self._txi.features.bumpmaptexture = args
                    self._empty = False
                elif command == TXICommand.BUMPYSHINYTEXTURE:
                    self._txi.features.bumpyshinytexture = args
                    self._empty = False
                elif command == TXICommand.CANDOWNSAMPLE:
                    self._txi.features.candownsample = bool(int(args))
                    self._empty = False
                elif command == TXICommand.CARETINDENT:
                    self._txi.features.caretindent = float(args)
                    self._empty = False
                elif command == TXICommand.CHANNELSCALE:
                    self._txi.features.channelscale = list(map(float, args.split()))
                    self._empty = False
                elif command == TXICommand.CHANNELTRANSLATE:
                    self._txi.features.channeltranslate = list(map(float, args.split()))
                    self._empty = False
                elif command == TXICommand.CLAMP:
                    self._txi.features.clamp = bool(int(args))
                    self._empty = False
                elif command == TXICommand.CODEPAGE:
                    self._txi.features.codepage = int(args)
                    self._empty = False
                elif command == TXICommand.COLS:
                    self._txi.features.cols = int(args)
                    self._empty = False
                elif command == TXICommand.COMPRESSTEXTURE:
                    self._txi.features.compresstexture = bool(int(args))
                    self._empty = False
                elif command == TXICommand.CONTROLLERSCRIPT:
                    self._txi.features.controllerscript = args
                    self._empty = False
                elif command == TXICommand.CUBE:
                    self._txi.features.cube = bool(int(args))
                    self._empty = False
                elif command == TXICommand.DECAL:
                    self._txi.features.decal = bool(int(args))
                    self._empty = False
                elif command == TXICommand.DEFAULTBPP:
                    self._txi.features.defaultbpp = int(args)
                    self._empty = False
                elif command == TXICommand.DEFAULTHEIGHT:
                    self._txi.features.defaultheight = int(args)
                    self._empty = False
                elif command == TXICommand.DEFAULTWIDTH:
                    self._txi.features.defaultwidth = int(args)
                    self._empty = False
                elif command == TXICommand.DISTORT:
                    try:
                        self._txi.features.distort = bool(int(args))
                    except ValueError:
                        self._txi.features.distort = float(args)
                    self._empty = False
                elif command == TXICommand.DISTORTANGLE:
                    self._txi.features.distortangle = float(args)
                    self._empty = False
                elif command == TXICommand.DISTORTIONAMPLITUDE:
                    self._txi.features.distortionamplitude = float(args)
                    self._empty = False
                elif command == TXICommand.DOWNSAMPLEFACTOR:
                    self._txi.features.downsamplefactor = float(args)
                    self._empty = False
                elif command == TXICommand.DOWNSAMPLEMAX:
                    self._txi.features.downsamplemax = int(args)
                    self._empty = False
                elif command == TXICommand.DOWNSAMPLEMIN:
                    self._txi.features.downsamplemin = int(args)
                    self._empty = False
                elif command == TXICommand.ENVMAPTEXTURE:
                    self._txi.features.envmaptexture = args
                    self._empty = False
                elif command == TXICommand.FILERANGE:
                    self._txi.features.filerange = list(map(int, args.split()))
                    self._empty = False
                elif command == TXICommand.FILTER:
                    self._txi.features.filter = bool(int(args))
                    self._empty = False
                elif command == TXICommand.FONTHEIGHT:
                    self._txi.features.fontheight = float(args)
                    self._empty = False
                elif command == TXICommand.FONTWIDTH:
                    self._txi.features.fontwidth = float(args)
                    self._empty = False
                elif command == TXICommand.FPS:
                    self._txi.features.fps = float(args)
                    self._empty = False
                elif command == TXICommand.ISBUMPMAP:
                    self._txi.features.isbumpmap = bool(int(args))
                    self._empty = False
                elif command == TXICommand.ISLIGHTMAP:
                    self._txi.features.islightmap = bool(int(args))
                    self._empty = False
                elif command == TXICommand.ISSPECULARBUMPMAP:
                    self._txi.features.isspecularbumpmap = bool(int(args))
                    self._empty = False
                elif command == TXICommand.LOWERRIGHTCOORDS:
                    self._txi.features.lowerrightcoords = [(0.0, 0.0, 0) for _ in range(int(args))]
                    mode = TXIReaderMode.LOWER_RIGHT_COORDS
                    cur_coords = 0
                    self._empty = False
                elif command == TXICommand.MAXSIZEHQ:
                    self._txi.features.maxSizeHQ = int(args)
                    self._empty = False
                elif command == TXICommand.MAXSIZELQ:
                    self._txi.features.maxSizeLQ = int(args)
                    self._empty = False
                elif command == TXICommand.MINSIZEHQ:
                    self._txi.features.minSizeHQ = int(args)
                    self._empty = False
                elif command == TXICommand.MINSIZELQ:
                    self._txi.features.minSizeLQ = int(args)
                    self._empty = False
                elif command == TXICommand.MIPMAP:
                    self._txi.features.mipmap = bool(int(args))
                    self._empty = False
                elif command == TXICommand.NUMCHARS:
                    self._txi.features.numchars = int(args)
                    self._empty = False
                elif command == TXICommand.NUMCHARSPERSHEET:
                    self._txi.features.numcharspersheet = int(args)
                    self._empty = False
                elif command == TXICommand.NUMX:
                    self._txi.features.numx = int(args)
                    self._empty = False
                elif command == TXICommand.NUMY:
                    self._txi.features.numy = int(args)
                    self._empty = False
                elif command == TXICommand.ONDEMAND:
                    self._txi.features.ondemand = bool(int(args))
                    self._empty = False
                elif command == TXICommand.PRIORITY:
                    self._txi.features.priority = int(args)
                    self._empty = False
                elif command == TXICommand.PROCEDURETYPE:
                    self._txi.features.proceduretype = args
                    self._empty = False
                elif command == TXICommand.ROWS:
                    self._txi.features.rows = int(args)
                    self._empty = False
                elif command == TXICommand.SPACINGB:
                    self._txi.features.spacingB = float(args)
                    self._empty = False
                elif command == TXICommand.SPACINGR:
                    self._txi.features.spacingR = float(args)
                    self._empty = False
                elif command == TXICommand.SPEED:
                    self._txi.features.speed = float(args)
                    self._empty = False
                elif command == TXICommand.TEMPORARY:
                    self._txi.features.temporary = bool(int(args))
                    self._empty = False
                elif command == TXICommand.TEXTUREWIDTH:
                    self._txi.features.texturewidth = float(args)
                    self._empty = False
                elif command == TXICommand.UNIQUE:
                    self._txi.features.unique = bool(int(args))
                    self._empty = False
                elif command == TXICommand.UPPERLEFTCOORDS:
                    self._txi.features.upperleftcoords = [(0.0, 0.0, 0) for _ in range(int(args))]
                    mode = TXIReaderMode.UPPER_LEFT_COORDS
                    cur_coords = 0
                    self._empty = False
                elif command == TXICommand.WATERHEIGHT:
                    self._txi.features.waterheight = float(args)
                    self._empty = False
                elif command == TXICommand.WATERWIDTH:
                    self._txi.features.waterwidth = float(args)
                    self._empty = False
                elif command == TXICommand.XBOX_DOWNSAMPLE:
                    self._txi.features.xbox_downsample = bool(int(args))
                    self._empty = False
            except Exception as e:  # noqa: BLE001
                RobustLogger().warning(f"Invalid TXI line: '{line}'", exc_info=e)

        return self._txi


class TXIBinaryWriter(ResourceWriter):
    def __init__(self, txi: TXI, target: TARGET_TYPES):
        super().__init__(target)
        self._txi: TXI = txi

    @autoclose
    def write(self, *, auto_close: bool = True):  # noqa: FBT001, FBT002, ARG002  # pyright: ignore[reportUnusedParameters]
        self._writer.write_string(str(self._txi))
