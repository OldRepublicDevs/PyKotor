from __future__ import annotations

import pathlib
import sys
import unittest
from configparser import ConfigParser

THIS_SCRIPT_PATH = pathlib.Path(__file__).resolve()
REPO_ROOT = THIS_SCRIPT_PATH.parents[4]
PYKOTOR_PATH = REPO_ROOT.joinpath("Libraries", "PyKotor", "src")


def add_sys_path(p: pathlib.Path) -> None:
    working_dir = str(p)
    if working_dir not in sys.path:
        sys.path.append(working_dir)


if PYKOTOR_PATH.joinpath("pykotor").exists():
    add_sys_path(PYKOTOR_PATH)

from pykotor.common.misc import Game
from pykotor.tslpatcher.config import PatcherConfig
from pykotor.tslpatcher.logger import PatchLogger
from pykotor.tslpatcher.memory import PatcherMemory
from pykotor.tslpatcher.mods.ncs import ModificationsNCS, ModifyNCS, NCSTokenType
from pykotor.tslpatcher.reader import ConfigReader


def _apply(filename: str, modifier: ModifyNCS, size: int = 32) -> bytes:
    data = bytearray(size)
    ModificationsNCS(filename, modifiers=[modifier]).apply(
        data, PatcherMemory(), PatchLogger(), Game.K1
    )
    return bytes(data)


def _load_hack_ini(ini_text: str) -> ModificationsNCS:
    parser = ConfigParser(
        delimiters="=",
        allow_no_value=True,
        strict=False,
        interpolation=None,
    )
    parser.optionxform = lambda optionstr: optionstr
    parser.read_string(ini_text)
    config = PatcherConfig()
    ConfigReader(parser, pathlib.Path("."), tslpatchdata_path=pathlib.Path(".")).load(config)
    assert config.patches_ncs, "expected a HACKList patch"
    return config.patches_ncs[0]


class TestHackListOfficialLongInt(unittest.TestCase):
    def test_unprefixed_ncs_write_is_four_byte_big_endian(self) -> None:
        data = _apply("test.ncs", ModifyNCS(NCSTokenType.UINT32, 8, 1234))
        self.assertEqual(data[8:12], bytes.fromhex("000004d2"))

    def test_unprefixed_non_ncs_write_is_four_byte_little_endian(self) -> None:
        data = _apply("test.dat", ModifyNCS(NCSTokenType.UINT32, 8, 1234))
        self.assertEqual(data[8:12], bytes.fromhex("d2040000"))

    def test_explicit_u16_prefix_stays_two_bytes(self) -> None:
        data = _apply("test.ncs", ModifyNCS(NCSTokenType.UINT16, 8, 1234))
        self.assertEqual(data[8:10], bytes.fromhex("04d2"))
        self.assertEqual(data[10:12], b"\x00\x00")

    def test_reader_defaults_unprefixed_value_to_uint32(self) -> None:
        modifications = _load_hack_ini(
            """
            [HACKList]
            File0=test.ncs

            [test.ncs]
            8=1234
            """
        )
        modifier = modifications.modifiers[0]
        self.assertEqual(modifier.token_type, NCSTokenType.UINT32)
        self.assertEqual(modifier.offset, 8)
        self.assertEqual(modifier.token_id_or_value, 1234)

    def test_reader_keeps_explicit_u16_prefix(self) -> None:
        modifications = _load_hack_ini(
            """
            [HACKList]
            File0=test.ncs

            [test.ncs]
            8=u16:1234
            """
        )
        modifier = modifications.modifiers[0]
        self.assertEqual(modifier.token_type, NCSTokenType.UINT16)
        self.assertEqual(modifier.token_id_or_value, 1234)


if __name__ == "__main__":
    unittest.main()
