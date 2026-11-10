"""Local due-diligence issues and durable, many-to-many voucher references."""

import json

from .voucher_notes import voucher_lines_for_key, voucher_signature


STATUSES = ("待核查", "待补资料", "已核查")
SCHEMA = """
CREATE TABLE IF NOT EXISTS dd_issues (
 id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL DEFAULT '待核查', description TEXT NOT NULL DEFAULT '',
 notes TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dd_voucher_refs (
 voucher_key TEXT PRIMARY KEY, label TEXT NOT NULL, summary TEXT NOT NULL,
 signature TEXT NOT NULL, snapshot_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dd_issue_vouchers (
 issue_id INTEGER NOT NULL REFERENCES dd_issues(id) ON DELETE CASCADE,
 voucher_key TEXT NOT NULL REFERENCES dd_voucher_refs(voucher_key),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(issue_id,voucher_key)
);
CREATE INDEX IF NOT EXISTS ix_dd_links_voucher ON dd_issue_vouchers(voucher_key);
"""


def voucher_label(lines):
    row = lines[0]
    scope = f'{row["fiscal_year"]}年{row["period"]}期' if row["fiscal_year"] and row["period"] else row["entry_date"]
    number = f'{row["voucher_type"]}{row["voucher_no"]}' if row["voucher_no"] else f'无凭证号（第{row["row_no"]}行）'
    return f"{scope} {number}".strip()


def list_issues(store):
    return [dict(row) for row in store.db.execute("""SELECT i.*,COUNT(v.voucher_key) AS count
        FROM dd_issues i LEFT JOIN dd_issue_vouchers v ON v.issue_id=i.id
        GROUP BY i.id ORDER BY i.id""")]


def create_issue(store, title):
    title = title.strip()
    if not title:
        raise ValueError("请输入问题名称")
    if store.db.execute("SELECT 1 FROM dd_issues WHERE title=?", (title,)).fetchone():
        raise ValueError("已有同名问题，请使用其他名称")
    with store.db:
        return store.db.execute("INSERT INTO dd_issues(title) VALUES(?)", (title,)).lastrowid


def get_issue(store, issue_id):
    row = store.db.execute("SELECT * FROM dd_issues WHERE id=?", (issue_id,)).fetchone()
    if row is None:
        raise ValueError("问题已不存在")
    return dict(row)


def save_issue(store, issue_id, revision, title, status, description, notes):
    title = title.strip()
    if not title:
        raise ValueError("问题名称不能为空")
    if status not in STATUSES:
        raise ValueError("处理状态无效")
    other = store.db.execute("SELECT id FROM dd_issues WHERE title=? AND id<>?", (title, issue_id)).fetchone()
    if other:
        raise ValueError("已有同名问题，请使用其他名称")
    with store.db:
        cursor = store.db.execute("""UPDATE dd_issues SET title=?,status=?,description=?,notes=?,revision=revision+1
            WHERE id=? AND revision=?""", (title, status, description, notes, issue_id, revision))
        if not cursor.rowcount:
            raise ValueError("问题已被其他窗口修改；当前输入已保留，请复制后重新打开问题核对")
    return revision + 1


def delete_issue(store, issue_id):
    with store.db:
        store.db.execute("DELETE FROM dd_issues WHERE id=?", (issue_id,))


def issue_vouchers(store, issue_id, offset=0, limit=100):
    # Do not deserialize snapshots or query every voucher when expanding a directory.
    return [dict(row) for row in store.db.execute("""SELECT r.voucher_key,r.label,r.summary
        FROM dd_issue_vouchers v JOIN dd_voucher_refs r USING(voucher_key)
        WHERE v.issue_id=? ORDER BY v.created_at,v.rowid LIMIT ? OFFSET ?""", (issue_id, limit, offset))]


def link_voucher(store, issue_id, key):
    get_issue(store, issue_id)
    lines = voucher_lines_for_key(store, key)
    existing = store.db.execute("SELECT 1 FROM dd_voucher_refs WHERE voucher_key=?", (key,)).fetchone()
    if not lines and not existing:
        raise ValueError("凭证已不存在，无法加入问题")
    with store.db:
        if lines:
            signature = voucher_signature(lines)
            store.db.execute("""INSERT OR IGNORE INTO dd_voucher_refs
                (voucher_key,label,summary,signature,snapshot_json) VALUES(?,?,?,?,?)""",
                (key, voucher_label(lines), lines[0]["summary"], signature,
                 json.dumps([dict(row) for row in lines], ensure_ascii=False)))
            # Never reset an existing mark's note or silently accept changed content.
            store.db.execute("INSERT OR IGNORE INTO voucher_marks(voucher_key,note,signature) VALUES(?,'',?)", (key, signature))
        cursor = store.db.execute("INSERT OR IGNORE INTO dd_issue_vouchers(issue_id,voucher_key) VALUES(?,?)", (issue_id, key))
        return bool(cursor.rowcount)


def unlink_voucher(store, issue_id, key):
    with store.db:
        store.db.execute("DELETE FROM dd_issue_vouchers WHERE issue_id=? AND voucher_key=?", (issue_id, key))


def voucher_preview(store, key):
    reference = store.db.execute("SELECT * FROM dd_voucher_refs WHERE voucher_key=?", (key,)).fetchone()
    live = voucher_lines_for_key(store, key)
    if not reference and not live:
        raise ValueError("原凭证未匹配")
    status = "正常"
    if not live:
        status = "原凭证未匹配（以下为归集时快照）"
    elif reference and voucher_signature(live) != reference["signature"]:
        status = "内容变化，待复核（以下为当前分录）"
    lines = [dict(row) for row in live] if live else json.loads(reference["snapshot_json"])
    return {"key": key, "label": voucher_label(lines), "lines": lines, "status": status,
            "entry_id": live[0]["id"] if live else None,
            "snapshot": json.loads(reference["snapshot_json"]) if reference else None}


def confirm_reference(store, key):
    lines = voucher_lines_for_key(store, key)
    if not lines:
        raise ValueError("原凭证未匹配，不能确认")
    with store.db:
        store.db.execute("""UPDATE dd_voucher_refs SET label=?,summary=?,signature=?,snapshot_json=?
            WHERE voucher_key=?""", (voucher_label(lines), lines[0]["summary"], voucher_signature(lines),
                                      json.dumps([dict(row) for row in lines], ensure_ascii=False), key))


def write_note(store, key, baseline, text):
    """Compare-and-set notes without updating a voucher's review signature."""
    with store.db:
        current = store.db.execute("SELECT note FROM voucher_marks WHERE voucher_key=?", (key,)).fetchone()
        value = current["note"] if current else None
        if value != baseline:
            raise ValueError("凭证关注或备注已被其他入口修改；当前输入已保留，请复制后重新打开核对")
        if current:
            cursor = store.db.execute("UPDATE voucher_marks SET note=?,updated_at=CURRENT_TIMESTAMP WHERE voucher_key=? AND note=?", (text, key, baseline))
            if not cursor.rowcount:
                raise ValueError("备注在保存时发生变化；当前输入已保留，请重新核对")
        else:
            lines = voucher_lines_for_key(store, key)
            if not lines:
                raise ValueError("原凭证未匹配，无法新建关注备注")
            store.db.execute("INSERT INTO voucher_marks(voucher_key,note,signature) VALUES(?,?,?)", (key, text, voucher_signature(lines)))


def note_record(store, key):
    return store.db.execute("SELECT note FROM voucher_marks WHERE voucher_key=?", (key,)).fetchone()


def reference_summary(store, key):
    return store.db.execute("SELECT label,summary FROM dd_voucher_refs WHERE voucher_key=?", (key,)).fetchone()


def entry_page_offset(store, entry_id, page_size=500):
    target = store.entry(entry_id)
    if target is None:
        return 0
    rank = store.db.execute("SELECT COUNT(*) FROM entries WHERE entry_date<? OR (entry_date=? AND id<?)",
                            (target["entry_date"], target["entry_date"], entry_id)).fetchone()[0]
    return rank // page_size * page_size
