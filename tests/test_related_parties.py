import tempfile
import unittest
from pathlib import Path

from ledger_viewer.importer import ImportSpec, guess_header
from ledger_viewer.related_parties import party_names, parse_party_names, party_names_from_file, related_party_match
from ledger_viewer.store import LedgerStore


class RelatedPartiesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.store = LedgerStore(self.base / "book.sqlite")
        source = self.base / "details.csv"
        source.write_text(
            "日期,凭证号,科目编码,科目名称,摘要,辅助核算,借方金额,贷方金额\n"
            "2026-08-01,1,2241,其他应付款,借款,VEN00001/中亿信息科技有限公司,100,0\n"
            "2026-08-02,2,2241,其他应付款,提到中亿信息科技有限公司,VEN00002/其他公司,50,0\n",
            encoding="utf-8",
        )
        header, _, mapping = guess_header(source, "CSV", "entry")
        self.store.import_sheet(ImportSpec(source, "CSV", header, "entry", mapping))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_manual_list_matches_exact_auxiliary_name_and_alias(self):
        party_id = self.store.save_related_party("中亿信息科技有限公司", ["中亿科技"], "客户确认，待核工商资料")
        parties = self.store.related_parties()
        entries = self.store.entries()
        self.assertEqual(related_party_match(entries[0], parties), "中亿信息科技有限公司")
        self.assertEqual(related_party_match(entries[1], parties), "")
        self.assertEqual(parties[0]["note"], "客户确认，待核工商资料")
        with self.assertRaises(ValueError):
            self.store.save_related_party("另一公司", ["中亿科技"])
        target = self.base / "details_export.csv"
        self.store.export_entries(target)
        self.assertIn("用户标注关联方", target.read_text(encoding="utf-8-sig"))
        self.assertIn("中亿信息科技有限公司", target.read_text(encoding="utf-8-sig"))
        self.store.delete_related_party(party_id)
        self.assertEqual(related_party_match(entries[0], self.store.related_parties()), "")

    def test_list_is_isolated_by_book(self):
        self.store.save_related_party("中亿信息科技有限公司", [])
        other = LedgerStore(self.base / "other.sqlite")
        try:
            self.assertEqual(other.related_parties(), [])
        finally:
            other.close()

    def test_party_names_flatten_aliases_without_notes_for_cloud_requests(self):
        self.store.save_related_party("乙公司", [])
        self.store.save_related_party("甲公司", ["甲集团", "ＡＣｏ"], "客户确认，此备注不发送")
        self.assertEqual(party_names(self.store.related_parties()), ["乙公司", "甲公司", "甲集团", "ACo"])

    def test_parse_party_names_skips_blanks_and_duplicates(self):
        text = "甲公司\n\n  乙公司 \n甲公司\nｃｏｓｔａ\n"
        self.assertEqual(parse_party_names(text), ["甲公司", "乙公司", "costa"])
        self.assertEqual(parse_party_names(""), [])

    def test_party_names_from_file_reads_first_nonempty_column_and_skips_header(self):
        csv_path = self.base / "parties.csv"
        csv_path.write_text("关联方名称,备注\n甲公司,股东\n乙公司,\n丙公司\n", encoding="utf-8")
        self.assertEqual(party_names_from_file(csv_path), ["甲公司", "乙公司", "丙公司"])
        txt_path = self.base / "names.txt"
        txt_path.write_text("甲公司\n乙公司\n甲公司\n", encoding="utf-8")
        self.assertEqual(party_names_from_file(txt_path), ["甲公司", "乙公司"])


if __name__ == "__main__":
    unittest.main()
