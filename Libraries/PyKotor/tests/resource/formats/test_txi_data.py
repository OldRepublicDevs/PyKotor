import unittest

from pykotor.resource.formats.txi.txi_data import TXI


class TestTXI(unittest.TestCase):
    def test_parse_distort_decimal(self):
        txi = TXI()
        txi.load("distort 0.1")
        self.assertEqual(txi.features.distort, 0.1)

    def test_parse_blending_default(self):
        self.assertEqual(TXI.parse_blending("default"), 0)
        self.assertEqual(TXI.parse_blending("DEFAULT"), 0)
        self.assertEqual(TXI.parse_blending("Default"), 0)

    def test_parse_blending_additive(self):
        self.assertEqual(TXI.parse_blending("additive"), 1)
        self.assertEqual(TXI.parse_blending("ADDITIVE"), 1)
        self.assertEqual(TXI.parse_blending("Additive"), 1)

    def test_parse_blending_punchthrough(self):
        self.assertEqual(TXI.parse_blending("punchthrough"), 2)
        self.assertEqual(TXI.parse_blending("PUNCHTHROUGH"), 2)
        self.assertEqual(TXI.parse_blending("Punchthrough"), 2)
        self.assertEqual(TXI.parse_blending("punch-through"), 2)

    def test_parse_blending_invalid(self):
        self.assertEqual(TXI.parse_blending("invalid"), 0)
        self.assertEqual(TXI.parse_blending(""), 0)
        self.assertEqual(TXI.parse_blending("blend"), 0)

    def test_parse_blending_case_insensitive(self):
        self.assertEqual(TXI.parse_blending("DeFaUlT"), 0)
        self.assertEqual(TXI.parse_blending("AdDiTiVe"), 1)
        self.assertEqual(TXI.parse_blending("PuNcHtHrOuGh"), 2)

    def test_parse_blending_numeric_values(self):
        self.assertEqual(TXI.parse_blending("0"), 0)
        self.assertEqual(TXI.parse_blending("1"), 1)
        self.assertEqual(TXI.parse_blending("2"), 2)

    def test_loaded_txi_parses_embedded_text(self):
        txi_text = (
            "proceduretype cycle\r\n"
            "numx 2\r\n"
            "numy 2\r\n"
            "fps 16\r\n"
            "blending additive\r\n"
            "downsamplemax 0\r\n"
            "downsamplemin 0"
        )
        txi = TXI(txi_text)

        self.assertEqual(txi.features.proceduretype, "cycle")
        self.assertEqual(txi.features.numx, 2)
        self.assertEqual(txi.features.numy, 2)
        self.assertEqual(txi.features.blending, 1)
        self.assertIn("blending additive", str(txi))
        self.assertNotIn("blending 1", str(txi))

    def test_generated_blending_uses_txi_keyword(self):
        txi = TXI()
        txi.features.blending = 1

        self.assertEqual(str(txi), "blending additive")

    def test_looks_like_txi_uses_full_command_enum(self):
        self.assertTrue(TXI.looks_like_txi("maxSizeHQ 64"))
        self.assertTrue(TXI.looks_like_txi("decal1"))
        self.assertFalse(TXI.looks_like_txi("notATxiCommand 64"))

    def test_tpc_source_txi_regenerates_when_features_change(self):
        from pykotor.resource.formats.tpc.tpc_data import TPC

        source = "proceduretype cycle\r\nnumx 2\r\nnumy 2\r\nfps 16\r\nblending additive"
        tpc = TPC()
        tpc.txi = source

        self.assertEqual(source, tpc.txi)
        tpc._txi.features.blending = 2  # noqa: SLF001

        self.assertIn("blending punchthrough", tpc.txi)
        self.assertNotEqual(source, tpc.txi)


if __name__ == "__main__":
    unittest.main()
