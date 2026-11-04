import tempfile
import unittest
from pathlib import Path

from ledger_viewer.importer import ImportSpec, guess_header
from ledger_viewer.related_parties import (party_names, party_entries_from_file, party_names_from_file,
                                           parse_party_lines, parse_party_names, parse_roles, party_roles,
                                           related_party_match, build_party_index, indexed_party_match, role_grouped_names)
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

    def test_indexed_batch_matching_preserves_existing_match_semantics(self):
        parties = [
            {"name": "甲公司", "aliases_json": '["Ａ公司", "共同别名"]'},
            {"name": "乙公司", "aliases_json": '["共同别名", "乙集团"]'},
            {"name": "丙公司", "aliases_json": "无效 JSON"},
        ]
        index = build_party_index(parties)
        for counterparty, auxiliary in (("a公司", ""), ("VEN00001/乙集团", ""),
                                         ("共同别名", ""), ("甲公司", "乙公司"),
                                         ("丙公司", "丙公司"), ("", ""), ("未知", "")):
            row = {"counterparty": counterparty, "auxiliary": auxiliary}
            self.assertEqual(indexed_party_match(row, index), related_party_match(row, parties))

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

    def test_roles_persist_and_grouped_payload_dedupes(self):
        party_id = self.store.save_related_party("赵六", [], "", None, ["股东", "实际控制人"], "创始人")
        self.store.save_related_party("张三", ["张三别名"], "", None, ["董事"], "")
        self.store.save_related_party("无身份公司", [], "", None, [], "客户")
        stored = {row["name"]: row for row in self.store.related_parties()}
        self.assertEqual(party_roles(stored["赵六"]), ["股东", "实际控制人"])
        self.assertEqual(stored["赵六"]["relation"], "创始人")
        self.assertEqual(party_roles(stored["无身份公司"]), [])
        grouped = role_grouped_names(self.store.related_parties())
        self.assertEqual(grouped["股东"], ["赵六"])
        self.assertEqual(grouped["实际控制人"], ["赵六"])  # 一个主体出现在每个身份下
        self.assertEqual(grouped["董事"], ["张三", "张三别名"])
        self.assertEqual(grouped["未注明身份"], ["无身份公司"])
        # 更新身份并保留关系
        self.store.save_related_party("赵六", [], "", party_id, ["前股东"], "创始人")
        self.assertEqual(party_roles(next(r for r in self.store.related_parties() if r["name"] == "赵六")), ["前股东"])

    def test_parse_roles_drops_unknown_words(self):
        self.assertEqual(parse_roles("股东,董事、监事"), ["股东", "董事", "监事"])
        self.assertEqual(parse_roles("法定代表人,股东"), ["股东"])  # 未知身份不猜测
        self.assertEqual(parse_roles(""), [])

    def test_party_lines_and_file_import_carry_roles(self):
        text = "张三｜股东,董事\n\n李四｜未知身份\n王五\n张三"
        self.assertEqual(parse_party_lines(text), [("张三", ["股东", "董事"]), ("李四", []), ("王五", [])])
        csv_path = self.base / "parties_roles.csv"
        csv_path.write_text("名称,身份,备注\n赵六,\"股东,实际控制人\",创始人\n乙公司,法人,无\n丙公司\n", encoding="utf-8")
        entries = party_entries_from_file(csv_path)
        self.assertEqual(entries, [("赵六", ["股东", "实际控制人"]), ("乙公司", []), ("丙公司", [])])


if __name__ == "__main__":
    unittest.main()
