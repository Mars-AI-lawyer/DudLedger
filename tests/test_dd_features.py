import tempfile
import unittest
from pathlib import Path

from ledger_viewer.dd_features import (FeatureContext, KEYWORD_GROUPS, closing_voucher, load_config,
                                        low_value_voucher, materiality_bases, mechanical_voucher, save_config,
                                        validate_config, voucher_feature)
from ledger_viewer.importer import ImportSpec, guess_header
from ledger_viewer.store import LedgerStore


def entry_row(**overrides):
    row = {"name": "管理费用", "summary": "日常支出", "counterparty": "", "auxiliary": "",
           "debit_cents": 100, "credit_cents": 0}
    row.update(overrides)
    return row


class DDFeaturesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.store = LedgerStore(self.base / "book.sqlite")
        source = self.base / "details.csv"
        source.write_text(
            "日期,凭证号,科目编码,科目名称,摘要,往来单位,借方金额,贷方金额\n"
            "2026-08-01,1,2241,其他应付款,支付股东借款,张三,100,0\n"
            "2026-08-01,1,1002,银行存款,支付股东借款,张三,0,100\n"
            "2026-08-02,2,6602,管理费用,报销办公费,乙公司,10,0\n"
            "2026-08-02,2,1002,银行存款,报销办公费,乙公司,0,10\n"
            "2026-08-03,3,1002,银行存款,往来款,丙公司,0,700\n", encoding="utf-8")
        header, _, mapping = guess_header(source, "CSV", "entry")
        self.store.import_sheet(ImportSpec(source, "CSV", header, "entry", mapping))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_keyword_groups_map_to_categories(self):
        context = FeatureContext(self.store)
        feature = voucher_feature(context, [entry_row(summary="支付履约保证金")])
        self.assertIn("DEPOSIT", feature["flags"])
        self.assertIn("guarantee", feature["rule_categories"])
        feature = voucher_feature(context, [entry_row(summary="劳动仲裁和解款")])
        self.assertIn("EMPLOYMENT_WORD", feature["flags"])
        self.assertIn("dispute", feature["rule_categories"])

    def test_party_match_fact_includes_roles(self):
        self.store.save_related_party("张三", [], "", None, ["股东", "董事"], "创始股东")
        context = FeatureContext(self.store)
        rows = [entry_row(summary="临时借款", counterparty="张三", debit_cents=5000000, credit_cents=0)]
        feature = voucher_feature(context, rows)
        self.assertIn("RELATED_PARTY", feature["flags"])
        self.assertTrue(any("张三" in fact and "股东" in fact for fact in feature["facts"]))

    def test_materiality_facts_from_report_rows_and_overrides(self):
        with self.store.db:
            source_id = self.store.db.execute(
                "INSERT INTO sources(kind,filename,original_path,sheet,sha256,mapping_json) VALUES('balance_sheet','bs.csv','x','S','h','{}')").lastrowid
            self.store.db.execute(
                "INSERT INTO report_rows(source_id,row_no,left_item,left_amount_1_cents,right_item,right_amount_1_cents,raw_json)"
                " VALUES(?,1,'资产总计',200000000,'所有者权益（或股东权益）合计',80000000,'[]')", (source_id,))
        bases = materiality_bases(self.store)
        self.assertEqual(bases["total_assets"], 200000000)
        self.assertEqual(bases["net_assets"], 80000000)
        context = FeatureContext(self.store)
        feature = voucher_feature(context, [entry_row(summary="付款", debit_cents=1000000, credit_cents=0)])
        # 1,000,000 元 / 80,000,000 元 = 1.25% ≥ 1% 关注比例
        self.assertTrue(any("净资产" in fact for fact in feature["facts"]))

    def test_config_validation_and_persistence(self):
        config = load_config(self.store)
        self.assertIsNone(config["amount_yuan"])  # 默认不设金额关注线
        with self.assertRaises(ValueError):
            validate_config({**config, "percentile": 0.9})
        with self.assertRaises(ValueError):
            validate_config({**config, "net_asset_pct": -1})
        with self.assertRaises(ValueError):
            validate_config({**config, "amount_yuan": -5})
        clean = validate_config({**config, "amount_yuan": None})
        self.assertIsNone(clean["amount_yuan"])
        save_config(self.store, {**config, "amount_yuan": 100, "percentile": 0.99})
        self.assertEqual(load_config(self.store)["percentile"], 0.99)
        self.assertEqual(load_config(self.store)["amount_yuan"], 100)
        # 旧版固定 500 万配置在加载时视为未自定义，回到“默认不设”
        self.store.save_setting("dd_filter_config", '{"amount_yuan": 5000000, "percentile": 0.95}')
        self.assertIsNone(load_config(self.store)["amount_yuan"])

    def test_amount_line_disabled_by_default_and_manual_value_applies(self):
        context = FeatureContext(self.store)
        self.assertEqual(context.amount_line_cents, 0)  # 默认不设：任何金额都不触发 LARGE_AMOUNT
        huge = [entry_row(summary="付款", debit_cents=900000000, credit_cents=0)]
        self.assertNotIn("LARGE_AMOUNT", voucher_feature(context, huge)["flags"])
        save_config(self.store, {**load_config(self.store), "amount_yuan": 1000})
        configured = FeatureContext(self.store)
        self.assertEqual(configured.amount_line_cents, 100000)
        feature = voucher_feature(configured, [entry_row(summary="付款", debit_cents=900000, credit_cents=0)])
        self.assertIn("LARGE_AMOUNT", feature["flags"])
        self.assertTrue(any("金额关注线" in fact for fact in feature["facts"]))

    def test_mechanical_and_low_value_gating(self):
        depreciation = [entry_row(name="累计折旧", summary="计提本月折旧", debit_cents=50, credit_cents=0),
                        entry_row(name="管理费用", summary="计提本月折旧", debit_cents=0, credit_cents=50)]
        self.assertTrue(mechanical_voucher(depreciation))
        context = FeatureContext(self.store)
        feature = voucher_feature(context, depreciation)
        self.assertTrue(low_value_voucher(depreciation, feature, context.amount_line_cents))
        # 摘要混入业务事项后不再视为机械凭证
        mixed = [entry_row(name="管理费用", summary="计提本月折旧并支付赔偿", debit_cents=0, credit_cents=50)]
        self.assertFalse(mechanical_voucher(mixed))
        # 大额折旧在设置了金额关注线时恢复送审；默认不设线时保持跳过
        big = [entry_row(name="累计折旧", summary="计提折旧", debit_cents=600000000, credit_cents=0),
               entry_row(name="管理费用", summary="计提折旧", debit_cents=0, credit_cents=600000000)]
        self.assertTrue(mechanical_voucher(big))
        self.assertFalse(low_value_voucher(big, voucher_feature(context, big), 500000000))
        closing = [entry_row(name="本年利润", summary="结转本期损益", debit_cents=10, credit_cents=0),
                   entry_row(name="主营业务收入", summary="结转本期损益", debit_cents=0, credit_cents=10)]
        self.assertTrue(closing_voucher(closing))

    def test_account_percentile_and_counterparty_rank(self):
        for index in range(12):
            with self.store.db:
                self.store.db.execute(
                    "INSERT INTO entries(source_id,row_no,entry_date,voucher_no,voucher_type,account_key,summary,"
                    "counterparty,auxiliary,debit_cents,credit_cents,raw_json) VALUES(1,?, '2026-08-04',?,'记',"
                    "'C:2241','小额往来','丁公司','',5,0,'[]')", (100 + index, str(100 + index)))
        context = FeatureContext(self.store)
        top = voucher_feature(context, [entry_row(name="其他应付款", summary="往来", counterparty="丙公司",
                                                  debit_cents=0, credit_cents=70000)])
        self.assertTrue(any("分位" in fact for fact in top["facts"]))
        self.assertTrue(any("丙公司" in fact and "排名" in fact for fact in top["facts"]))

    def test_keyword_groups_have_categories(self):
        ids = {topic for _flag, _label, _words, topic in KEYWORD_GROUPS}
        from ledger_viewer.jev_rules import TOPIC_IDS
        self.assertTrue(ids <= set(TOPIC_IDS))


if __name__ == "__main__":
    unittest.main()
