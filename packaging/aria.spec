# PyInstaller spec — builds dist/Aria (Aria.exe + its files). Run through packaging/build.py.
# What's left out, and why, is measured in packaging/SIZE_REPORT.md (packaging/size_report.py).
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent
VLC = ROOT / "vlc-3.0.18"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, SPECPATH)
import vlc_plugins  # noqa: E402

datas = [(str(ROOT / "assets" / f), "assets") for f in ("icon.ico", "icon.png", "icon.svg", "icon-small.svg")]
datas += [(str(ROOT / "aria" / "core" / "t2s.txt"), "aria/core")]
# libVLC as plain files: its plugins are loaded by VLC itself, not something PyInstaller should analyse.
# Only the plugins an audio player uses (packaging/vlc_plugins.py).
datas += [(str(VLC / f), "vlc-3.0.18") for f in ("libvlc.dll", "libvlccore.dll", "COPYING.txt")]
for f in sorted((VLC / "plugins").rglob("*")):
    rel = f.relative_to(VLC / "plugins")
    if f.is_file() and vlc_plugins.keep(rel.as_posix()):
        datas.append((str(f), str(Path("vlc-3.0.18/plugins") / rel.parent)))
datas += collect_data_files("yt_dlp_ejs")
datas += copy_metadata("yt-dlp") + copy_metadata("yt-dlp-ejs")      # versions compared by the updater

# yt-dlp ships a PyInstaller hook (yt_dlp.__pyinstaller) for building yt-dlp itself: not a runtime module,
# and it would pull PyInstaller into the app
hiddenimports = collect_submodules("yt_dlp", filter=lambda name: not name.startswith("yt_dlp.__pyinstaller")) \
    + collect_submodules("yt_dlp_ejs")

excludes = [
    "PyInstaller",                       # see above
    "werkzeug", "markupsafe",            # websockets' server-side router; yt-dlp uses its client only
    "yaml",                              # numpy.__config__.show()
    "psutil",                            # numpy.testing
    "simplejson",                        # requests uses the standard json without it
    "cffi", "win32ctypes",               # Cryptodome falls back to ctypes; PyInstaller's own helper
    "setuptools", "pkg_resources", "_distutils_hack",   # build tools, never imported by Aria
    "PySide6.QtNetwork",                 # Aria's network goes through requests / yt-dlp
    # only on a machine with more installed than .venv-build (see packaging/build.py)
    "tkinter", "PyQt5", "PyQt6", "matplotlib", "IPython", "pandas", "cv2", "PIL", "scipy", "pytest",
]

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

# Qt pieces Aria never loads: software OpenGL (it draws with the raster engine), Qt's translations
# (English UI), platforms other than Windows, the Windows style (Aria sets Fusion), the TUIO touch
# protocol and image formats it never shows (covers are JPEG / PNG / WebP / BMP, the icon ICO / SVG).
QT_UNUSED = ("opengl32sw.dll", "plugins/platforms/qdirect2d.dll", "plugins/platforms/qminimal.dll",
             "plugins/platforms/qoffscreen.dll", "plugins/styles/", "plugins/generic/",
             "plugins/imageformats/qicns.dll", "plugins/imageformats/qtga.dll",
             "plugins/imageformats/qtiff.dll", "plugins/imageformats/qwbmp.dll")


def unused(dest: str) -> bool:
    d = dest.replace("\\", "/")
    if d.startswith("PySide6/translations/"):
        return True
    if d.startswith("PySide6/") and any(d[len("PySide6/"):].startswith(u) for u in QT_UNUSED):
        return True
    return d == "libvlccore.dll"         # a second copy found next to python-vlc: VLC loads the one in vlc-3.0.18


a.binaries = [b for b in a.binaries if not unused(b[0])]
a.datas = [d for d in a.datas if not unused(d[0])]
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
