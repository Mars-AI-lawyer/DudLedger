"""Build an allowlisted, self-contained macOS application."""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build" / "mac"


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True, cwd=ROOT)


def main():
    if sys.platform != "darwin":
        raise SystemExit("Build this application on macOS.")
    WORK.mkdir(parents=True, exist_ok=True)
    stage = WORK / "source"
    if stage.exists():
        shutil.rmtree(stage)
    package = stage / "ledger_viewer"
    package.mkdir(parents=True)
    # Explicit source allowlist: never recursively copy the repository or HOME.
    for source in sorted((ROOT / "ledger_viewer").glob("*.py")):
        shutil.copyfile(source, package / source.name)
    shutil.copyfile(ROOT / "run.py", stage / "run.py")
    for name in ("specialist_rules.json", "skills/dudledger-import/SKILL.md", "resources/icon-beta.png"):
        target = package / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "ledger_viewer" / name, target)
    iconset = WORK / "DudLedger.iconset"
    iconset.mkdir(exist_ok=True)
    icon_source = package / "resources" / "icon-beta.png"
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            suffix = "@2x" if scale == 2 else ""
            run("sips", "-z", size * scale, size * scale,
                icon_source, "--out", iconset / f"icon_{size}x{size}{suffix}.png")
    run("iconutil", "-c", "icns", iconset, "-o", WORK / "DudLedger.icns")
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", ROOT / "dist", "--workpath", WORK / "pyinstaller",
        ROOT / "packaging" / "mac.spec")
    runtime = ROOT / "dist" / "mac-runtime"
    if runtime.exists():
        runtime_target = WORK / "mac-runtime"
        if runtime_target.exists():
            shutil.rmtree(runtime_target)
        shutil.move(str(runtime), str(runtime_target))
    run(sys.executable, ROOT / "scripts" / "audit_mac.py",
        ROOT / "dist" / "DudLedger.app", WORK / "安全检查.json")
    run("codesign", "--verify", "--deep", "--strict", ROOT / "dist" / "DudLedger.app")
    # Replace the distributable only after the new app passes inspection.
    from ledger_viewer import __version__
    architecture = "Apple芯片" if platform.machine() == "arm64" else "Intel"
    archive = ROOT / "dist" / f"DudLedger-{__version__}-Mac-{architecture}.zip"
    temporary = WORK / "portable.zip"
    run("ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", ROOT / "dist" / "DudLedger.app", temporary)
    temporary.replace(archive)
    print(f"Packaged {archive.name}")
    print(f"Built dist/DudLedger.app ({platform.machine()})")


if __name__ == "__main__":
    main()
