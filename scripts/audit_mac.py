"""Inspect bundle files and embedded Python constants without reading credentials."""
from __future__ import annotations

import hashlib
import json
import marshal
import re
import sys
import types
import zipfile
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader

TOKEN = re.compile(rb"(?<![A-Za-z0-9])(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[A-Z0-9]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")
FORBIDDEN = {'.sqlite', '.sqlite3', '.db', '.xlsx', '.xls', '.xlsm', '.csv', '.tsv', '.log', '.pem', '.key', '.p12', '.pfx'}


def audit_bundle(app: Path, binary: Path, required: set[str], output: Path, *, sample_files=frozenset()):
    failures, manifest = [], []

    def scan(label, data, *, allow_sqlite=False):
        if TOKEN.search(data) or (not allow_sqlite and data.startswith(b'SQLite format 3\0')):
            failures.append(f"Sensitive content signature: {label}")

    def constants(label, code):
        # Marshal exposes nested tuples/frozensets and nested code as plain bytes too.
        scan(label, marshal.dumps(code))

    files = sorted(path for path in app.rglob('*') if path.is_file())
    for path in files:
        relative = path.relative_to(app)
        name = relative.as_posix()
        if path.is_symlink():
            if not path.resolve().is_relative_to(app):
                failures.append(f"External symlink: {name}")
            continue
        parts = set(relative.parts)
        if ((path.suffix.lower() in FORBIDDEN and relative not in sample_files) or path.name.startswith('.env') or
                parts.intersection({'.git', '.venv', '.dudledger', '.workbuddy', 'examples', 'docs', 'tests'}) or
                path.name in {'preferences.json', '.DS_Store', '.git-auto.json'}):
            failures.append(f"Forbidden file: {name}")
        data = path.read_bytes()
        scan(name, data, allow_sqlite=relative in sample_files and path.suffix == '.sqlite')
        if path.suffix == '.zip':
            with zipfile.ZipFile(path) as archive:
                for member in archive.namelist():
                    blob = archive.read(member)
                    scan(name + ':' + member, blob)
                    if member.endswith('.pyc'):
                        constants(member, marshal.loads(blob[16:]))
        manifest.append({'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})

    archive = CArchiveReader(str(binary))
    module_names = []
    for name, entry in archive.toc.items():
        if entry[-1] == 'z':
            pyz = archive.open_embedded_archive(name)
            for module in pyz.toc:
                module_names.append(module)
                code = pyz.extract(module)
                if isinstance(code, types.CodeType):
                    constants(module, code)
                if module == 'tests' or module.startswith('tests.'):
                    failures.append(f"Test module: {module}")
        elif entry[-1] == 's':
            constants(name, marshal.loads(archive.extract(name)))
    for module in required - set(module_names):
        failures.append(f"Missing module: {module}")
    report = {'status': 'passed' if not failures else 'failed',
              'checks': ['文件白名单与禁带文件检查', '文件内容、标准库及内嵌模块密钥特征扫描',
                         'SQLite 文件头检查', '外部符号链接检查', '必要运行模块检查'],
              'scope': '仅检查成品；不读取系统钥匙串及个人配置。不进行联网模型请求。',
              'regular_files': len(manifest), 'embedded_modules': len(module_names),
              'failures': failures, 'files': manifest}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'files'}, ensure_ascii=False))
    if failures:
        raise SystemExit(1)


def main():
    app = Path(sys.argv[1]).resolve()
    required = {'ledger_viewer.ui', 'ledger_viewer.agent_import', 'openpyxl', 'xlrd',
                'keyring.backends.macOS', 'typesafe_sdk'}
    audit_bundle(app, app / 'Contents/MacOS' / app.stem, required, Path(sys.argv[2]))


if __name__ == '__main__':
    main()
