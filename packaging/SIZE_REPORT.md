# Aria 打包體積報告（v2.36.1）

依〈程式體積優化與 Windows EXE 打包規範（移除語音模型後）〉實施。所有數字都是實測，量測方式與原始資料見文末。

## 環境

| 項目 | 值 |
|---|---|
| 原始碼 | `b52d2cf` + 本次修改（未 commit） |
| Python | 3.13.7（x64） |
| PyInstaller | 6.14.2（hooks-contrib 2025.5） |
| Inno Setup | 6（`lzma2/max`、`SolidCompression`，設定未改） |
| Windows | Windows 11 x64（10.0.26300），開發機 |
| Baseline | 同一份原始碼，用原本的 spec、從開發用 Python（裝有許多其他套件）打包 |
| Candidate | 新 spec，從專用環境 `.venv-build`（`packaging/requirements-build.txt`，版本與 baseline 相同）打包 |

## 結果

| 指標 | Baseline | Candidate | 差異 |
|---|---:|---:|---:|
| `dist/Aria/` 實際檔案總量 (MiB) | 236.09 | 147.95 | −88.14（−37.3 %） |
| 檔案數 | 1,635 | 1,364 | −271 |
| `Aria.exe` (MiB) | 8.51 | 6.52 | −1.99（−23.4 %） |
| Inno Setup EXE (MiB) | 66.67 | 45.29 | −21.39（−32.1 %） |
| VLC 外掛 (MiB) | 111.88 | 39.60 | −72.27（−64.6 %） |
| VLC 核心 (MiB) | 2.88 | 2.88 | 0 |
| Qt / PySide6 (MiB) | 47.36 | 43.62 | −3.74（−7.9 %） |
| NumPy (MiB) | 26.03 | 26.03 | 0（BPM 分析需要，保留） |
| Python Runtime (MiB) | 23.76 | 17.08 | −6.68（−28.1 %） |
| yt-dlp / EJS (MiB) | 10.01 | 10.00 | 0（保留完整 extractor 與更新能力） |
| 其他 (MiB) | 5.42 | 1.96 | −3.46 |
| 第一次啟動 → 視窗出現 (s) | 4.56 | 4.94 | 各 1 次樣本，在誤差內（見下） |
| 暖啟動 → 視窗出現 (s，中位數 ×3) | 2.53 | 2.56 | ≈ 0 |
| 啟動 → 開始播放 (s，中位數 ×4，續播 YouTube) | 10.94 | 10.74 | ≈ 0（主要是網路解析） |
| 閒置 RAM，private (MiB，中位數) | 201.2 | 182.6 | −18.6 |
| 播放中 RAM，private (MiB，中位數) | 238.1 | 218.8 | −19.4 |
| 播放中 CPU (%，10 秒平均) | 0.12 | 0.10 | ≈ 0 |
| 建立一個 VLC 實例 (s) | 0.20–0.24 | 0.08–0.10 | 約 −60 %（掃描的外掛變少） |

「第一次啟動」每個版本只有一次樣本（剛打包完、Windows Defender 會先掃描新檔），差 0.4 秒在誤差範圍內；重複啟動的中位數兩者相同。

## 移除了什麼、為什麼

每一項都先找出「誰把它帶進來」（PyInstaller cross-reference），確認 Aria 執行時不會用到才移除。

| 移除 | 大小 | 證據 |
|---|---:|---|
| VLC 外掛 228 個（`packaging/vlc_plugins.py`） | 72.3 MiB | 視訊輸出／濾鏡／色彩轉換、視覺化、d3d、字幕與文字繪製、服務探索、控制介面、lua、壓縮檔解開；視訊編解碼（x264/x265/vpx/aom/dav1d…）、圖片、硬體視訊解碼；光碟、擷取裝置、遠端桌面、SRT/RIST 等；串流**輸出**（只留 BPM 用的 file）；Aria 不開的格式（SID、GME、MOD、MIDI）。所有音訊解碼器（含 WMA 需要的 avcodec）、音訊 demuxer、音訊濾鏡、輸出、HLS/DASH、TLS 都保留 |
| PyInstaller 本身、altgraph、importlib_metadata | ~1 MiB | 由 yt-dlp 附帶的 `yt_dlp.__pyinstaller`（打包 yt-dlp 用的 hook）帶入；收集 yt-dlp 子模組時排除這個 hook |
| setuptools 與其 vendored 套件 | ~1 MiB | 只被 setuptools 自己與 PyInstaller 的 setuptools runtime hook 引用；baseline 也排除了它 |
| werkzeug、markupsafe | ~1 MiB | websockets 的 server 端 router；yt-dlp 只用 client |
| yaml、psutil | <1 MiB | `numpy.__config__` 與 `numpy.testing` |
| simplejson、cffi、win32ctypes、pywin32 | <1 MiB | requests／Cryptodome 都有內建 fallback；pywin32 是開發環境的套件 |
| QtNetwork、TLS 與 networkinformation 外掛 | ~3 MiB | Aria 的網路全走 requests／yt-dlp；程式只 import QtCore/QtGui/QtWidgets/QtSvg |
| 第二套 OpenSSL（`libcrypto-3-x64` / `libssl-3-x64`） | 6.5 MiB | 隨 Qt 的 OpenSSL TLS 外掛帶入；Python 自己的 `_ssl` 用 `libcrypto-3.dll` |
| 重複的 `libvlccore.dll`（`_internal` 根目錄） | 2.7 MiB | VLC 從 `vlc-3.0.18` 載入自己的那份 |
| Qt 平台 qdirect2d／qminimal／qoffscreen、Windows style、TUIO 觸控、ICNS/TGA/TIFF/WBMP 圖片格式 | ~2 MiB | 只用 `qwindows`；Aria 設定 Fusion style；封面是 JPEG／PNG／WebP／BMP，圖示是 ICO／SVG（保留 qjpeg、qwebp、qgif、qico、qsvg、qsvgicon） |

沒有使用 UPX 或 onefile；layout 仍是 PyInstaller onedir（`Aria.exe` + `_internal`），所以 Inno Setup 的 `{app}\_internal` 清理規則不需改動。

## 回歸測試（實際執行）

**VLC（`vlcprobe`，以 Aria 的同樣參數重現四種用法）**：對完整 VLC、以及實際放進 `dist/Aria` 的 VLC 各跑一次，29 個情境結果完全相同，VLC 實際載入的 37 個模組也完全相同：

- 本機檔：MP3、MP2、M4A/AAC、FLAC、WAV、OGG/Vorbis、Opus、WebM/Opus、MKA、WMA：播放、`start-time` 續播、Seek
- 串流：YouTube、Spotify→YouTube、Bilibili（m4s + Referer）、SoundCloud（HLS AAC）：播放、Seek
- BPM：本機與串流 5 種來源都轉出 WAV（`#transcode` → `mux=wav`）
- 檔案驗證（`media_check`）：每個本機檔都讀得到長度；DirectSound 輸出裝置列舉相同（6 個）

**打包後的 Aria.exe（不操作畫面，只讀取）**：續播自動開始（狀態列「Playing」）；Tempo Flow 的背景子行程（`--multiprocessing-fork`）以精簡後的 VLC 算出 BPM；未完成的下載自動續傳、通過驗證並放進資料夾；畫面截圖中封面（JPEG／WebP）、SVG 圖示、Fusion 樣式、歌詞與 BPM 顯示都正常；`data/packages/` 中較新的 yt-dlp 會被載入（以匯入時寫出的標記檔確認）；記錄檔沒有錯誤。原始碼測試 107 個全部通過。

## 尚未驗證

- **乾淨的 Windows**（沒有 Python／VLC 的虛擬機）：只在開發機上測過。DLL 都在 `dist/Aria` 內、VLC 外掛路徑由 `PYTHON_VLC_MODULE_PATH` 指定，但規範要求的乾淨環境測試還沒做。
- **安裝檔的安裝、升級與解除安裝**：只確認編譯成功，沒有實際安裝（會寫入開始選單與登錄檔）。
- **縮放 125／150 %、深色模式、Mini Player、Collage 等的逐一比對**：沒有改動任何程式碼或主題，Qt 移除的是平台外掛以外的元件，但未逐一截圖比對。
- **真正的冷啟動**（重開機後第一次）：未量測。

## 量測方式與原始資料

- 體積：`python packaging/size_report.py --label <名稱> [--compare baseline]`，遞迴加總實際檔案位元組（硬連結只算一次），分類統計並列出前 50 大檔案與增減的檔案。結果：`packaging/size/baseline.*`、`packaging/size/candidate.*`（JSON + Markdown）
- 效能：啟動後以 UI Automation 偵測主視窗出現與狀態列變成「Playing」（續播、音量 0），再量 10 秒的 CPU 與整個行程樹的 private memory；第一次啟動 1 次、暖啟動 3 次、播放 4 次，取中位數。結果：`packaging/size/perf_*.json`
- VLC：以除錯記錄抓出每個情境 VLC 實際使用的模組。結果：`packaging/size/vlc_probe_full.json`（完整 VLC）、`vlc_probe_shipped.json`（實際發行的 VLC）
- 要恢復完整的 VLC 外掛：把 `packaging/vlc_plugins.py` 的 `PRUNE` 改成 `False` 後重新打包
