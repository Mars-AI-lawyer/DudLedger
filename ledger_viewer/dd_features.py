"""Program-computed voucher facts: the program measures, Jev only judges relevance.

金额重大性口径是律师筛选默认参数，不是审计或法律上的重大性标准。
"""
from __future__ import annotations

import bisect
import json
import re
import unicodedata

from .importer import format_money
from .related_parties import party_roles, related_party_matches, role_grouped_names

# 关键词只作为本地线索，最终是否值得律师查看由 Jev 结合完整凭证判断。
KEYWORD_GROUPS = (
    ("DEPOSIT", "摘要涉及保证金／押金类款项", ("保证金", "押金", "诚意金"), "guarantee"),
    ("LOAN_WORD", "摘要涉及借款类往来", ("借款", "拆借", "垫款", "垫资", "代付", "暂借", "备用金"), "financing"),
    ("CAPITAL_WORD", "摘要涉及出资／股权变动", ("实缴", "增资", "减资", "股权转让", "资本公积", "股权款", "老股转让", "验资"), "capital_equity"),
    ("INVESTMENT_WORD", "摘要涉及投资／收购", ("投资款", "收购款", "长期股权投资", "股权投资", "并购"), "investment"),
    ("GUARANTEE_WORD", "摘要涉及担保安排", ("担保", "保函", "抵押", "质押", "反担保", "代偿"), "guarantee"),
    ("DISPUTE_WORD", "摘要涉及争议／赔偿", ("诉讼", "仲裁", "赔偿", "补偿", "违约金", "和解", "律师费", "强制执行"), "dispute"),
    ("IP_WORD", "摘要涉及知识产权／技术", ("商标", "专利", "著作权", "版权", "许可费", "授权费", "技术服务费", "研发费"), "ip_rd"),
    ("EMPLOYMENT_WORD", "摘要涉及劳动用工", ("离职补偿", "经济补偿", "竞业", "劳动仲裁", "劳动争议", "社保补缴", "工伤"), "employment"),
    ("GOVERNMENT_WORD", "摘要涉及政府／监管", ("政府补贴", "财政奖励", "财政补贴", "专项资金", "行政罚款", "滞纳金", "行政处罚"), "government"),
)
KEYWORD_CATEGORY = {flag: category for flag, _label, _words, category in KEYWORD_GROUPS}

CONFIG_KEY = "dd_filter_config"
BASIS_KEY = "dd_basis_overrides"
# 金额关注线默认不设（amount_yuan=None）；仅在设置中手动填写后才启用。
DEFAULT_CONFIG = {"amount_yuan": None, "net_asset_pct": 1.0, "asset_pct": 0.5,
                  "revenue_pct": 0.5, "percentile": 0.95}
MIN_ACCOUNT_LINES = 10  # 样本太少的科目不宣称分位线
MIN_COUNTERPARTIES = 3

_NET_ASSET_ITEMS = {"所有者权益合计", "股东权益合计", "所有者权益(或股东权益)合计", "所有者权益（或股东权益）合计",
                    "所有者权益总计", "股东权益总计", "净资产合计"}
_TOTAL_ASSET_ITEMS = {"资产总计", "资产合计"}


def _normalize_item(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


def _revenue_item(text: str) -> bool:
    value = _normalize_item(text)
    value = re.sub(r"^[一二三四五六七八九十1-9][、.．]", "", value)
    return value in {"营业收入", "营业收入合计", "营业收入总额", "营业总收入"}


def load_config(store) -> dict:
    config = dict(DEFAULT_CONFIG)
    try:
        stored = json.loads(store.setting(CONFIG_KEY, "{}"))
    except (TypeError, ValueError):
        stored = {}
    if isinstance(stored, dict):
        for key in config:
            value = stored.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                config[key] = stored[key]
    # 旧版把固定 500 万写进配置；等于旧默认值视为从未自定义，回到“默认不设”。
    if config["amount_yuan"] == 5_000_000:
        config["amount_yuan"] = None
    return config


def validate_config(config: dict) -> dict:
    clean = dict(DEFAULT_CONFIG)
    amount = config.get("amount_yuan")
    if amount is not None and (not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount <= 0):
        raise ValueError("金额关注线只能是正数，留空表示不设")
    clean["amount_yuan"] = None if amount is None else float(amount)
    for key in ("net_asset_pct", "asset_pct", "revenue_pct"):
        value = config.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise ValueError("筛选参数必须是不小于 0 的数字")
        clean[key] = float(value)
    percentile = config.get("percentile")
    if percentile not in (0.95, 0.99):
        raise ValueError("科目分位线只能是 P95 或 P99")
    clean["percentile"] = float(percentile)
    return clean


def save_config(store, config: dict) -> None:
    clean = validate_config(config)
    store.save_setting(CONFIG_KEY, json.dumps(clean, ensure_ascii=False))


def load_basis_overrides(store) -> dict:
    try:
        stored = json.loads(store.setting(BASIS_KEY, "{}"))
    except (TypeError, ValueError):
        return {}
    result = {}
    if isinstance(stored, dict):
        # registered_capital 仅供旧数据兼容读取；重存时自然丢弃，不再参与计算。
        for key in ("net_assets", "total_assets", "revenue"):
            value = stored.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                result[key] = value
    return result


def save_basis_overrides(store, overrides: dict) -> None:
    clean = load_basis_overrides(store)
    for key in ("net_assets", "total_assets", "revenue"):
        value = overrides.get(key)
        if value in (None, ""):
            clean.pop(key, None)
        elif isinstance(value, int) and value > 0:
            clean[key] = value
        else:
            raise ValueError("报表基数只能填正整数金额（分），留空表示自动读取")
    store.save_setting(BASIS_KEY, json.dumps(clean, ensure_ascii=False))


def _statement_amount(store, kind: str, matches) -> tuple[int, str] | None:
    """取最新一份报表中首个匹配项目；金额列优先用核对方案选定的列。"""
    row = store.db.execute("SELECT id FROM sources WHERE kind=? ORDER BY id DESC LIMIT 1", (kind,)).fetchone()
    if row is None:
        return None
    source_id = row["id"]
    column = 1
    try:
        from .reconciliation import load_plan

        plan = load_plan(store)
        if isinstance(plan.get(kind), dict) and plan[kind].get("source_id") == source_id:
            column = plan[kind].get("amount_column", 1)
    except Exception:
        pass
    for report in store.db.execute("SELECT * FROM report_rows WHERE source_id=? ORDER BY row_no,id", (source_id,)):
        for side in ("left", "right"):
            item = report[f"{side}_item"]
            if not item or not matches(item):
                continue
            columns = (column, 1, 2, 3)
            for index in dict.fromkeys(columns):
                value = report[f"{side}_amount_{index}_cents"]
                if value is not None:
                    return value, f"{item.strip()}（来源 #{source_id}）"
    return None


def materiality_bases(store) -> dict:
    """净资产／总资产／营业收入基数：报表优先，允许手动覆盖；缺失时对应占比不计算。"""
    bases = {}
    for key, kind, matches in (
        ("total_assets", "balance_sheet", lambda item: _normalize_item(item) in _TOTAL_ASSET_ITEMS),
        ("net_assets", "balance_sheet", lambda item: _normalize_item(item) in _NET_ASSET_ITEMS),
        ("revenue", "income_statement", _revenue_item),
    ):
        found = _statement_amount(store, kind, matches)
        if found:
            bases[key] = found[0]
    bases.update(load_basis_overrides(store))
    return bases


class FeatureContext:
    """一次批次内复用的全局统计：主体名单、科目分位、交易对手金额与重大性基数。"""

    def __init__(self, store):
        self.store = store
        self.parties = store.related_parties()
        self.grouped_parties = role_grouped_names(self.parties)
        self.config = load_config(store)
        self.bases = materiality_bases(store)
        self.amount_line_cents = self._resolve_amount_line_cents()
        account_amounts: dict[str, list[int]] = {}
        for row in store.db.execute("""SELECT a.name account,
            MAX(ABS(e.debit_cents),ABS(e.credit_cents)) amount FROM entries e
            JOIN accounts a ON a.key=e.account_key WHERE MAX(ABS(e.debit_cents),ABS(e.credit_cents))>0"""):
            account_amounts.setdefault(row["account"], []).append(row["amount"])
        self.account_amounts = {name: sorted(values) for name, values in account_amounts.items()}
        counterparty_totals: dict[str, int] = {}
        for row in store.db.execute("""SELECT COALESCE(NULLIF(counterparty,''),'') name,
            SUM(MAX(ABS(debit_cents),ABS(credit_cents))) total FROM entries
            WHERE COALESCE(NULLIF(counterparty,''),'')<>'' GROUP BY name"""):
            counterparty_totals[row["name"]] = row["total"]
        self.counterparty_totals = counterparty_totals
        self.counterparty_total = sum(counterparty_totals.values())
        ranking = sorted(counterparty_totals, key=lambda name: (-counterparty_totals[name], name))
        self.counterparty_rank = {name: index + 1 for index, name in enumerate(ranking)}

    def _resolve_amount_line_cents(self) -> int:
        """金额关注线默认不设（0＝关闭）；仅在设置中手动填写后启用。"""
        explicit = self.config.get("amount_yuan")
        if isinstance(explicit, (int, float)) and not isinstance(explicit, bool) and explicit > 0:
            return int(round(explicit * 100))
        return 0

    def account_percentile(self, account: str, amount: int) -> float | None:
        values = self.account_amounts.get(account)
        if not values or len(values) < MIN_ACCOUNT_LINES or amount <= 0:
            return None
        return bisect.bisect_right(values, amount) / len(values)


def voucher_amount(lines) -> int:
    debit = sum(abs(line["debit_cents"]) for line in lines)
    credit = sum(abs(line["credit_cents"]) for line in lines)
    return max(debit, credit)


def _max_line(lines):
    return max(lines, key=lambda line: max(abs(line["debit_cents"]), abs(line["credit_cents"])), default=None)


def voucher_feature(context: FeatureContext, lines) -> dict:
    """计算一张凭证的程序特征：程序只陈述事实，判断留给 Jev 与律师。"""
    flags: list[str] = []
    facts: list[str] = []

    matched = []
    seen = set()
    for line in lines:
        for party in related_party_matches(line, context.parties):
            if party["name"] not in seen:
                seen.add(party["name"])
                matched.append(party)
    if matched:
        flags.append("RELATED_PARTY")
        described = []
        for party in matched[:3]:
            roles = party_roles(party)
            described.append(party["name"] + (f"（{'、'.join(roles)}）" if roles else ""))
        facts.append("往来对象命中尽调主体名单：" + "；".join(described) + ("等" if len(matched) > 3 else ""))

    summaries = "；".join(line["summary"] for line in lines if line["summary"])
    for flag, label, words, _category in KEYWORD_GROUPS:
        if any(word in summaries for word in words):
            flags.append(flag)
            facts.append(label)

    amount = voucher_amount(lines)
    if amount >= context.amount_line_cents and context.amount_line_cents > 0:
        flags.append("LARGE_AMOUNT")
        facts.append(f"凭证金额 {format_money(amount)} 元，达到金额关注线")

    for key, base, threshold, title in (
        ("net_assets", "net_assets", context.config["net_asset_pct"], "净资产"),
        ("total_assets", "total_assets", context.config["asset_pct"], "总资产"),
        ("revenue", "revenue", context.config["revenue_pct"], "营业收入"),
    ):
        value = context.bases.get(base)
        if value and amount > 0:
            pct = amount / value * 100
            if pct >= threshold:
                facts.append(f"凭证金额约占{title} {pct:.2g}%（达到关注比例）")

    top = _max_line(lines)
    if top is not None:
        top_amount = max(abs(top["debit_cents"]), abs(top["credit_cents"]))
        percentile = context.account_percentile(top["name"], top_amount)
        if percentile is not None and percentile >= context.config["percentile"]:
            flags.append("LARGE_PERCENTILE")
            label = "P99" if context.config["percentile"] >= 0.99 else "P95"
            facts.append(f"最大分录金额 {format_money(top_amount)} 元，进入该科目单笔金额 {label} 分位")

    reported = 0
    for name in dict.fromkeys(line["counterparty"].strip() for line in lines if line["counterparty"] and line["counterparty"].strip()):
        if len(context.counterparty_totals) < MIN_COUNTERPARTIES or reported >= 2:
            break
        rank = context.counterparty_rank.get(name)
        total = context.counterparty_totals.get(name, 0)
        share = total / context.counterparty_total * 100 if context.counterparty_total else 0
        if rank is not None and (rank <= 5 or share >= 5):
            facts.append(f"交易对手“{name}”全年金额排名第 {rank} 位（约 {share:.0f}%）")
            reported += 1

    rule_categories: set[str] = set()
    for flag in flags:
        if flag == "RELATED_PARTY":
            rule_categories.add("related_party")
        elif flag in KEYWORD_CATEGORY:
            rule_categories.add(KEYWORD_CATEGORY[flag])
    return {"flags": flags, "facts": facts, "amount_cents": amount, "rule_categories": sorted(rule_categories)}


def closing_voucher(rows) -> bool:
    """Only skip an entire unambiguous profit/loss closing voucher."""
    names = [re.sub(r'\s+', '', r['name']) for r in rows]
    if '本年利润' not in names or not any(n != '本年利润' for n in names):
        return False
    if sum(r['debit_cents'] for r in rows) != sum(r['credit_cents'] for r in rows):
        return False
    if not all(n == '本年利润' or n.startswith(('主营业务收入', '其他业务收入', '营业外收入',
            '主营业务成本', '其他业务成本', '营业外支出', '管理费用', '销售费用', '财务费用',
            '研发费用', '税金及附加', '营业税金及附加', '所得税费用', '投资收益', '公允价值变动损益',
            '资产减值损失', '信用减值损失', '资产处置收益', '其他收益')) for n in names):
        return False
    pattern = r'(?:[0-9年月期末本当度\-/.]*)(?:自动)?(?:(?:结转|结平)(?:本期|本月|本年|年度|当期|期末|年末)?(?:损益|利润|收入|成本|费用|成本费用|本年利润)|(?:损益|利润)结转)[。；; ]*'
    return all(re.fullmatch(pattern, re.sub(r'\s+', '', r['summary'])) for r in rows)


_DEPRECIATION_SUMMARY = re.compile(r'(?:计提|摊销|折旧|结转)[^；;]{0,12}?(?:折旧|摊销)[。；; ]*')
_FX_SUMMARY = re.compile(r'(?:汇兑|结转汇兑|汇兑损益|汇兑差额)[。；; ]*')


def mechanical_voucher(rows) -> bool:
    """结转损益、计提折旧摊销、汇兑自动结转等机械会计处理；界限从严，宁送勿漏。"""
    if closing_voucher(rows):
        return True
    summaries = [re.sub(r'\s+', '', r['summary']) for r in rows]
    names = [re.sub(r'\s+', '', r['name']) for r in rows]
    if sum(r['debit_cents'] for r in rows) != sum(r['credit_cents'] for r in rows):
        return False
    if all(_DEPRECIATION_SUMMARY.fullmatch(s) for s in summaries) and any(
            '累计折旧' in n or '累计摊销' in n or '长期待摊费用' in n for n in names):
        return True
    if all(_FX_SUMMARY.fullmatch(s) for s in summaries) and any('财务费用' in n or '汇兑' in n for n in names):
        return True
    return False


def low_value_voucher(rows, feature: dict, amount_line_cents: int) -> bool:
    """机械处理且无任何异常特征、金额未达关注线才免送；出现异常特征立即恢复送审。"""
    if not mechanical_voucher(rows):
        return False
    if feature["flags"]:
        return False
    if amount_line_cents > 0 and feature["amount_cents"] >= amount_line_cents:
        return False
    return True
