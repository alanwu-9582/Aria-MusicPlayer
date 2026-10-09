"""Bilibili via its web API (yt-dlp gets HTTP 412 without browser cookies)."""

from __future__ import annotations

import hashlib
import html
import os
import re
import threading
import time
import urllib.parse

from aria.core.models import BILIBILI, Stream, Track
from aria.providers import http, ytdlp
from aria.providers.base import Provider, audio_template

API = "https://api.bilibili.com"
REFERER = "https://www.bilibili.com/"
_BV = re.compile(r"(BV[0-9A-Za-z]{10})")
_AV = re.compile(r"\bav(\d+)", re.IGNORECASE)
_MIXIN = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49, 33, 9, 42,
          19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51,
          30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52]


def _clean(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def _https(url: str) -> str:
    return "https:" + url if url.startswith("//") else url.replace("http://", "https://", 1)


def _seconds(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    total = 0
    for part in str(value or "").split(":"):
        total = total * 60 + int(part or 0)
    return float(total)


class _Client:
    """Cookie + WBI-signature state, shared by all threads."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cookies: dict[str, str] = {}
        self._mixin_key = ""
        self._key_time = 0.0

    def _prepare(self) -> None:
        with self._lock:
            s = http.session()
            if not self._cookies:
                data = http.get(f"{API}/x/frontend/finger/spi").json()["data"]
                self._cookies = {"buvid3": data["b_3"], "buvid4": data["b_4"]}
            for k, v in self._cookies.items():
                s.cookies.set(k, v, domain=".bilibili.com")
            if time.time() - self._key_time > 6 * 3600:
                img = http.get(f"{API}/x/web-interface/nav", headers={"Referer": REFERER}).json()["data"]["wbi_img"]
                raw = "".join(u.rsplit("/", 1)[1].split(".")[0] for u in (img["img_url"], img["sub_url"]))
                self._mixin_key = "".join(raw[i] for i in _MIXIN)[:32]
                self._key_time = time.time()

    def get(self, path: str, params: dict, signed: bool = True) -> dict:
        self._prepare()
        if signed:
            params = dict(params, wts=int(time.time()))
            params = {k: "".join(c for c in str(v) if c not in "!'()*") for k, v in sorted(params.items())}
            query = urllib.parse.urlencode(params)
            params["w_rid"] = hashlib.md5((query + self._mixin_key).encode()).hexdigest()
        j = http.get(f"{API}{path}", params=params, headers={"Referer": REFERER}).json()
        if j.get("code") != 0:
            raise RuntimeError(f"Bilibili: {j.get('message') or j.get('code')}")
        return j.get("data") or {}

    @property
    def headers(self) -> dict[str, str]:
        return {"Referer": REFERER, "User-Agent": http.USER_AGENT}


def _split_id(track_id: str) -> tuple[str, int]:
    """"BVxxx" or "BVxxx?p=3" → (bvid, page index from 1)."""
    bvid, _, page = track_id.partition("?p=")
    return bvid, int(page or 1)


class Bilibili(Provider):
    source = BILIBILI
    searchable = True

    def __init__(self):
        self.api = _Client()

    def match(self, url: str) -> bool:
        return bool(re.search(r"bilibili\.com|b23\.tv", url)) or bool(_BV.fullmatch(url.strip()))

    def search(self, query: str, limit: int = 20) -> list[Track]:
        params = {"search_type": "video", "keyword": query, "page": 1}
        data = {}
        # Either endpoint may answer with a risk-control voucher instead of results.
        for path, signed in (("/x/web-interface/search/type", False), ("/x/web-interface/wbi/search/type", True)):
            data = self.api.get(path, params, signed=signed)
            if "result" in data:
                break
        out = []
        for v in data.get("result") or []:
            bvid = v.get("bvid")
            if not bvid:
                continue
            out.append(Track(source=BILIBILI, id=bvid, title=_clean(v.get("title")),
                             artist=_clean(v.get("author")), url=f"https://www.bilibili.com/video/{bvid}",
                             thumbnail=_https(v.get("pic", "")), duration=_seconds(v.get("duration"))))
        return out[:limit]

    def _view(self, bvid: str) -> dict:
        return self.api.get("/x/web-interface/wbi/view", {"bvid": bvid})

    def parse(self, url: str) -> list[Track]:
        url = url.strip()
        if "b23.tv" in url:
            url = http.session().head(url, allow_redirects=True, timeout=http.TIMEOUT).url
        m = _BV.search(url)
        if m:
            view = self._view(m.group(1))
        else:
            av = _AV.search(url)
            if not av:
                raise ValueError("無法辨識的 Bilibili 連結")
            view = self.api.get("/x/web-interface/wbi/view", {"aid": av.group(1)})
        bvid = view["bvid"]
        pages = view.get("pages") or [{}]
        artist = view.get("owner", {}).get("name", "")
        cover = _https(view.get("pic", ""))
        page_q = re.search(r"[?&]p=(\d+)", url)
        if len(pages) == 1 or page_q:
            idx = int(page_q.group(1)) if page_q else 1
            tid = bvid if idx == 1 else f"{bvid}?p={idx}"
            title = view["title"] if len(pages) == 1 else f"{view['title']} · {pages[idx - 1].get('part', idx)}"
            return [Track(source=BILIBILI, id=tid, title=_clean(title), artist=artist,
                          url=f"https://www.bilibili.com/video/{bvid}" + (f"?p={idx}" if idx > 1 else ""),
                          thumbnail=cover, duration=float(pages[idx - 1].get("duration") or view.get("duration") or 0))]
        return [Track(source=BILIBILI, id=bvid if i == 1 else f"{bvid}?p={i}", title=_clean(p.get("part") or view["title"]),
                      artist=artist, url=f"https://www.bilibili.com/video/{bvid}?p={i}", thumbnail=cover,
                      duration=float(p.get("duration") or 0))
                for i, p in enumerate(pages, 1)]

    def stream(self, track: Track) -> Stream:
        bvid, page = _split_id(track.id)
        pages = self._view(bvid).get("pages") or []
        if not pages:
            raise RuntimeError("找不到分 P")
        cid = pages[min(page, len(pages)) - 1]["cid"]
        data = self.api.get("/x/player/wbi/playurl", {"bvid": bvid, "cid": cid, "fnval": 16, "fnver": 0, "fourk": 0})
        audios = (data.get("dash") or {}).get("audio") or []
        if audios:
            url = max(audios, key=lambda a: a.get("bandwidth", 0))["baseUrl"]
        elif data.get("durl"):
            url = data["durl"][0]["url"]
        else:
            raise RuntimeError("找不到可播放的音訊")
        return Stream(url=url, headers=self.api.headers, expires_at=ytdlp.expiry_of(url, 1800))

    def download(self, track: Track, progress=None) -> str:
        stream = self.stream(track)
        path = audio_template(track).replace("%(ext)s", "m4a")
        tmp = path + ".part"
        with http.session().get(stream.url, headers=stream.headers, stream=True, timeout=30) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(256 * 1024):
                    f.write(chunk)
                    done += len(chunk)
                    if progress and total:
                        progress(done / total)
        os.replace(tmp, path)
        return path
