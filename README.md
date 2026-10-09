Aria MusicPlayer
======

![license MIT](https://img.shields.io/badge/license-MIT-blue)
![python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)

> 2023 &copy; alanwu-9852

專案簡介
---
播放、收藏與下載串流音樂的桌面播放器，支援 YouTube、Bilibili、Spotify、SoundCloud，並會自動推薦「相關」的歌曲接著播。

支援平台
---

| 平台 | 搜尋 | 貼連結 | 播放方式 |
|---|---|---|---|
| YouTube | ✓ | 影片、Shorts、播放清單 | 直接串流 |
| Bilibili | ✓ | 影片、分 P、b23.tv 短網址 | 直接串流 |
| Spotify | – | 歌曲、專輯、播放清單 | 讀取歌曲資訊後，自動對應到 YouTube 播放（Spotify 音訊有 DRM） |
| SoundCloud | ✓ | 歌曲、歌單 | 直接串流 |
| 其他網站 | – | yt-dlp 支援的連結 | 直接串流 |
| 本機檔案 | – | 「收藏 › 加入檔案…」 | 直接播放 |

自動推薦
---
播放列右側的 ✦ 開啟時，佇列播完會自動接上推薦的歌；「播放中 › 推薦」可以隨時查看與挑選。

- 推薦一律來自 YouTube。正在播的若是 Bilibili、Spotify 等其他平台，會先找出對應的 YouTube 影片當作推薦依據。
- 推薦的是**相關**而不是**相同**：同一首歌的翻唱、Live、Remix、歌詞版、重新上傳、簡繁不同的標題，都視為「同一首」並排除；已播過或已在佇列中的歌也不會再出現。
- 以目前這首為主、最近播過的歌為輔，並避免同一位歌手連續出現。

使用說明
---
執行環境：Windows 10 / 11，Python 3.10 以上（專案已附 VLC 3.0.18）。

```bash
pip install -r requirements.txt
python main.py
```

YouTube 經常改版。若出現「無法播放」，在「主控台」輸入 `update` 更新 yt-dlp 後重新啟動即可。若電腦裝有 [Deno](https://deno.com) 或 Node.js，yt-dlp 會用它解 YouTube 的簽章，相容性更好。

### 介面

- **播放中**：封面、佇列（可拖曳排序）、推薦、播放紀錄
- **搜尋**：輸入關鍵字，或直接貼上任何支援的連結；歌單連結可一次加入佇列或收藏
- **收藏**：收藏的歌、下載（可離線播放）、匯入／匯出歌單（相容 v1 的 JSON）
- **主控台**：即時紀錄與指令列，輸入 `help` 查看所有指令

所有按鈕的說明都在滑鼠停留的提示裡；在清單上按右鍵有完整動作選單。

### 快捷鍵

| 按鍵 | 作用 |
|---|---|
| 空白鍵 | 播放／暫停 |
| Ctrl + ← / → | 上一首／下一首 |
| Ctrl + ↑ / ↓ | 音量 ± 5 |
| Ctrl + M | 靜音 |
| Ctrl + D | 收藏目前的歌 |
| Ctrl + F | 搜尋 |
| Ctrl + O | 加入本機音樂檔 |
| Ctrl + 1 – 4 | 切換頁面 |
| Enter / Delete | 播放／移除選取的歌 |

### 資料

收藏、佇列、設定、下載的音檔與快取都放在 `data/`。第一次啟動時會自動匯入 v1 的 `assets/saved.json` 與 `assets/queue.json`。

專案結構
---

```
aria/
  core/        資料模型、播放（VLC）、佇列與推薦、收藏與下載、儲存
  providers/   各平台的搜尋、連結解析、串流與下載
  ui/          PySide6 介面：主題 token、元件、頁面
tests/         標題比對、推薦過濾、歌單匯入的測試（pytest）
```

介面依循〈介面設計規範〉：色彩只透過 token 取用、跟隨系統深淺色、同列控制項等高 30、圓角不超過短邊 1/3、畫面上不放操作說明。

本專案強調色：淺色 `#507768`、深色 `#93bead`（HSL 157° / 20%，依規範 §1.3 一次抽定）。

版本更新
---
* v1.0.0-beta (2023/11/12): 測試版
* v1.0.1-beta (2024/01/18): Bug Fixed
    1. 修復了重新載入後須重啟才可更新音樂名稱
* v2.0.0 (2026/10/09): 全面重構
    1. 改用 PySide6 重新設計介面，支援深色模式
    2. 新增 Bilibili、Spotify、SoundCloud 與本機檔案
    3. 新增自動推薦（相關而非相同）
    4. 串流網址在播放時才取得並快取，不再需要「重新加載」；下一首會預先載入
    5. 播放紀錄、上一首、重複播放、拖曳排序、篩選收藏
    6. 主控台新增指令列

致謝
---
- 圖示形狀取自 [Lucide](https://lucide.dev)（ISC License）
- 簡繁對照表取自 [OpenCC](https://github.com/BYVoid/OpenCC)（Apache License 2.0）
- 播放核心為 [VLC](https://www.videolan.org)，解析使用 [yt-dlp](https://github.com/yt-dlp/yt-dlp)

如果你有任何建議或問題，請隨時聯繫作者
