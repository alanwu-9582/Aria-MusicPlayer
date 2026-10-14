"""Build Aria.exe (dist/Aria) and its installer (dist/Aria-Setup-<version>.exe).

    python packaging/build.py              # both
    python packaging/build.py --no-installer

Needs PyInstaller (pip install pyinstaller) and, for the installer, Inno Setup 6.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
sys.path.insert(0, str(ROOT))

from aria import __version__  # noqa: E402

VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(filevers={v}, prodvers={v}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'alanwu-9582'),
      StringStruct('FileDescription', 'Aria'),
      StringStruct('FileVersion', '{s}'),
      StringStruct('InternalName', 'Aria'),
      StringStruct('OriginalFilename', 'Aria.exe'),
      StringStruct('ProductName', 'Aria'),
      StringStruct('ProductVersion', '{s}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def iscc() -> str | None:
    env = os.environ.get
    candidates = [Path(env("LOCALAPPDATA", "")) / "Programs", Path(env("ProgramFiles(x86)", "")), Path(env("ProgramFiles", ""))]
    found = [c / "Inno Setup 6" / "ISCC.exe" for c in candidates if (c / "Inno Setup 6" / "ISCC.exe").exists()]
    return shutil.which("ISCC") or (str(found[0]) if found else None)


def main() -> int:
    parts = [int(p) for p in __version__.split(".")][:4]
    BUILD.mkdir(exist_ok=True)
    (BUILD / "version_info.txt").write_text(
        VERSION_INFO.format(v=tuple(parts + [0] * (4 - len(parts))), s=__version__), encoding="utf-8")

    try:
        shutil.rmtree(DIST / "Aria", ignore_errors=False)
    except FileNotFoundError:
        pass
    except PermissionError:
        print("dist/Aria is in use - close Aria.exe (check the tray) and build again")
        return 1
    print(f"Building Aria {__version__} ...", flush=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", str(ROOT / "packaging" / "aria.spec"), "--noconfirm",
                    "--clean", "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller")], check=True)
    print(f"-> {DIST / 'Aria' / 'Aria.exe'}", flush=True)

    if "--no-installer" in sys.argv:
        return 0
    compiler = iscc()
    if not compiler:
        print("Inno Setup 6 not found — skipped the installer (https://jrsoftware.org/isdl.php)")
        return 1
    subprocess.run([compiler, f"/DAppVersion={__version__}", str(ROOT / "packaging" / "aria.iss")], check=True)
    print(f"-> {DIST / f'Aria-Setup-{__version__}.exe'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
