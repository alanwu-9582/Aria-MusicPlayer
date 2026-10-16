"""Size report of a built Aria folder (dist/Aria by default): the real bytes on disk, by category.

    python packaging/size_report.py --label baseline              # writes build/size/baseline.json / .md
    python packaging/size_report.py --label candidate --compare baseline
    python packaging/size_report.py --dir "C:/Program Files/Aria" --label installed

Counts every file once (hard links to the same file are counted once and reported), lists the
50 largest files, and sums by extension and by category: VLC core / VLC plugins (per plugin
folder), Qt / PySide6, NumPy, yt-dlp / EJS, Python runtime, Aria, other. ``--compare`` adds the
difference to an earlier report. The installer (dist/Aria-Setup-*.exe) is measured when present.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "build" / "size"
MIB = 1024 * 1024


def category(rel: str) -> str:
    p = rel.replace("\\", "/").lower()
    if p == "aria.exe":
        return "Aria.exe"
    p = p.removeprefix("_internal/")
    if p.startswith("vlc-3.0.18/plugins/"):
        return "VLC plugins"
    if p.startswith("vlc-3.0.18/"):
        return "VLC core"
    if p.startswith(("pyside6/", "shiboken6/")):
        return "Qt / PySide6"
    if p.startswith(("numpy/", "numpy.libs/", "numpy-")):
        return "NumPy"
    if p.startswith(("yt_dlp", "yt-dlp")):
        return "yt-dlp / EJS"
    if p.startswith(("aria/", "assets/")):
        return "Aria"
    if p.startswith(("python3", "base_library.zip", "vcruntime", "msvcp", "ucrtbase", "api-ms-")) or \
            (p.endswith(".pyd") and "/" not in p) or p in ("libffi-8.dll", "sqlite3.dll") or p.startswith(("libcrypto", "libssl")):
        return "Python runtime"
    return "Other"


def scan(folder: Path) -> dict:
    files, seen_ids, links = [], set(), 0
    for dirpath, _dirs, names in os.walk(folder):
        for n in names:
            f = Path(dirpath) / n
            st = f.stat()
            ident = (st.st_dev, st.st_ino)
            if st.st_nlink > 1 and ident in seen_ids:
                links += 1
                continue                                  # the same file again (hard link): counted once
            seen_ids.add(ident)
            files.append((str(f.relative_to(folder)), st.st_size))
    by_cat, by_ext, by_plugin = defaultdict(int), defaultdict(int), defaultdict(int)
    for rel, size in files:
        by_cat[category(rel)] += size
        by_ext[(Path(rel).suffix.lower() or "(none)")] += size
        parts = rel.replace("\\", "/").split("/")
        if "plugins" in parts and "vlc-3.0.18" in parts:
            i = parts.index("plugins")
            by_plugin[parts[i + 1] if i + 2 < len(parts) else "(files)"] += size
    report = {
        "folder": str(folder), "total_bytes": sum(s for _r, s in files), "files": len(files),
        "hard_links_skipped": links,
        "aria_exe_bytes": next((s for r, s in files if r.lower() == "aria.exe"), 0),
        "categories": dict(sorted(by_cat.items(), key=lambda kv: -kv[1])),
        "extensions": dict(sorted(by_ext.items(), key=lambda kv: -kv[1])[:15]),
        "vlc_plugin_folders": dict(sorted(by_plugin.items(), key=lambda kv: -kv[1])),
        "largest": [{"path": r, "bytes": s} for r, s in sorted(files, key=lambda x: -x[1])[:50]],
        "file_list": sorted(r.replace("\\", "/") for r, _s in files),
    }
    setup = sorted((folder.parent).glob("Aria-Setup-*.exe"), key=lambda p: p.stat().st_mtime)
    if setup:
        report["installer"] = {"name": setup[-1].name, "bytes": setup[-1].stat().st_size}
    return report


def environment() -> dict:
    def git(*args):
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        except OSError:
            return ""
    try:
        import PyInstaller
        pyi = PyInstaller.__version__
    except ImportError:
        pyi = ""
    return {"commit": git("rev-parse", "--short", "HEAD"), "dirty": bool(git("status", "--porcelain")),
            "python": sys.version.split()[0], "pyinstaller": pyi,
            "windows": platform.platform(), "machine": platform.machine()}


def mib(b: int) -> str:
    return f"{b / MIB:,.2f}"


def markdown(r: dict, base: dict | None) -> str:
    def row(name, now, before):
        if before is None:
            return f"| {name} | {mib(now)} |"
        d = now - before
        pct = f"{100 * d / before:+.1f} %" if before else "—"
        return f"| {name} | {mib(before)} | {mib(now)} | {d / MIB:+,.2f} | {pct} |"

    head = "| | MiB |\n|---|---:|" if base is None else \
        "| | Baseline (MiB) | Candidate (MiB) | Δ (MiB) | Δ |\n|---|---:|---:|---:|---:|"
    lines = [f"# Aria size report · {r['label']}", "",
             f"`{r['folder']}` · {r['files']:,} files · built from {r['env']['commit']}"
             f"{' (with local changes)' if r['env']['dirty'] else ''} · Python {r['env']['python']} · "
             f"PyInstaller {r['env']['pyinstaller']} · {r['env']['windows']}", "", head]
    b = base or {}
    lines.append(row("**Folder total**", r["total_bytes"], b.get("total_bytes") if base else None))
    lines.append(row("Aria.exe", r["aria_exe_bytes"], b.get("aria_exe_bytes") if base else None))
    if "installer" in r:
        lines.append(row(f"Installer ({r['installer']['name']})", r["installer"]["bytes"],
                         b.get("installer", {}).get("bytes") if base else None))
    for cat in sorted((set(r["categories"]) | set(b.get("categories", {}))) - {"Aria.exe"},
                      key=lambda c: -r["categories"].get(c, 0)):
        lines.append(row(cat, r["categories"].get(cat, 0), b.get("categories", {}).get(cat, 0) if base else None))
    lines += ["", "## VLC plugin folders", "", head]
    for k in sorted(set(r["vlc_plugin_folders"]) | set(b.get("vlc_plugin_folders", {})),
                    key=lambda c: -max(r["vlc_plugin_folders"].get(c, 0), b.get("vlc_plugin_folders", {}).get(c, 0))):
        lines.append(row(k, r["vlc_plugin_folders"].get(k, 0), b.get("vlc_plugin_folders", {}).get(k, 0) if base else None))
    if base:
        gone = sorted(set(base["file_list"]) - set(r["file_list"]))
        new = sorted(set(r["file_list"]) - set(base["file_list"]))
        lines += ["", f"## Files removed ({len(gone)}) / added ({len(new)})", ""]
        lines += [f"- − `{f}`" for f in gone[:400]] + ([f"- … {len(gone) - 400} more"] if len(gone) > 400 else [])
        lines += [f"- + `{f}`" for f in new[:100]]
    lines += ["", "## 50 largest files", "", "| MiB | File |", "|---:|---|"]
    lines += [f"| {mib(x['bytes'])} | `{x['path']}` |" for x in r["largest"]]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(ROOT / "dist" / "Aria"))
    ap.add_argument("--label", default="current")
    ap.add_argument("--compare", default="")
    a = ap.parse_args()
    folder = Path(a.dir)
    if not folder.is_dir():
        print(f"No folder at {folder} — build first (python packaging/build.py)")
        return 1
    r = scan(folder) | {"label": a.label, "env": environment()}
    base = json.loads((OUT / f"{a.compare}.json").read_text(encoding="utf-8")) if a.compare else None
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{a.label}.json").write_text(json.dumps(r, indent=1, ensure_ascii=False), encoding="utf-8")
    md = markdown(r, base)
    (OUT / f"{a.label}.md").write_text(md, encoding="utf-8")
    print(md.split("\n## VLC")[0])
    print(f"-> {OUT / (a.label + '.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
