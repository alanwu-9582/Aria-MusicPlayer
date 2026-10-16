# PyInstaller spec — builds dist/Aria (Aria.exe + its files). Run through packaging/build.py.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent
VLC = ROOT / "vlc-3.0.18"
sys.path.insert(0, str(ROOT))

datas = [(str(ROOT / "assets" / f), "assets") for f in ("icon.ico", "icon.png", "icon.svg", "icon-small.svg")]
datas += [(str(ROOT / "aria" / "core" / "t2s.txt"), "aria/core")]
# libVLC as plain files: its plugins are loaded by VLC itself, not something PyInstaller should analyse
datas += [(str(VLC / f), "vlc-3.0.18") for f in ("libvlc.dll", "libvlccore.dll", "COPYING.txt")]
datas += [(str(VLC / "plugins"), "vlc-3.0.18/plugins")]
datas += collect_data_files("yt_dlp_ejs")
datas += copy_metadata("yt-dlp") + copy_metadata("yt-dlp-ejs")      # versions compared by the updater

hiddenimports = collect_submodules("yt_dlp") + collect_submodules("yt_dlp_ejs")

# Packages that happen to be installed next to Aria's but that it never uses.
excludes = ["tkinter", "PyQt5", "PyQt6", "matplotlib", "IPython", "jupyter_client", "jupyter_core",
            "pandas", "cv2", "sympy", "PIL", "torch", "tensorflow", "scipy", "pytest", "setuptools", "pkg_resources",
            "pygments", "docutils", "notebook", "zmq", "tornado", "pyarrow", "aiohttp", "pydantic", "cryptography"]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    # as plain .py files, so a newer yt-dlp in data/packages can take their place (aria.paths)
    module_collection_mode={"yt_dlp": "py", "yt_dlp_ejs": "py"},
    noarchive=False,
)
# Qt's software OpenGL (Aria draws with the raster engine) and Qt's own translations (English UI)
a.binaries = [b for b in a.binaries if not b[0].endswith("opengl32sw.dll")]
a.datas = [d for d in a.datas if not d[0].replace("\\", "/").startswith("PySide6/translations/")]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Aria",
    icon=str(ROOT / "assets" / "icon.ico"),
    version=str(ROOT / "build" / "version_info.txt"),
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Aria", upx=False)
