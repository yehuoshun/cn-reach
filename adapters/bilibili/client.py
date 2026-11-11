"""B站请求层：cookie / 重试退避 / wbi 签名 / 错误归一化。

参考实现：
  - yehuoshun/bili-digest  scripts/bili_subtitle.py   (wbi 签名、字幕、热评兜底)
  - yehuoshun/interest-radar scripts/bili_radar/client.py (指数退避 / 429 退避 / -412 熔断)
两者均 MIT，本文件为精简重写，非直接复制。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.bilibili.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
REFERER = "https://www.bilibili.com/"

# wbi 混淆表（B站前端同款，固定值）
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]

BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")


class BiliError(RuntimeError):
    """统一的适配器错误：带 errno，方便上层决定降级策略。"""

    def __init__(self, message: str, code: int | None = None, kind: str = "api"):
        super().__init__(message)
        self.code = code
        self.kind = kind  # api | risk | network | auth | not_found


def cookie_path() -> Path:
    return Path(os.environ.get(
        "CNREACH_BILI_COOKIE",
        os.environ.get("BILI_COOKIE_FILE", str(Path.home() / ".openclaw/secrets/bilibili-cookies.txt")),
    ))


def load_cookie() -> str:
    """读 cookie；没有就返回空串（部分只读接口不登录也能用）。"""
    p = cookie_path()
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def parse_bvid(text: str) -> str:
    m = BV_RE.search(text)
    if not m:
        raise BiliError(f"没找到 BV 号: {text}", kind="not_found")
    return m.group(0)


def get_json(url: str, cookie: str = "", tries: int = 3, timeout: int = 15):
    """GET → JSON，带退避。

    - 网络异常 / 5xx：指数退避重试
    - 429：尊重 Retry-After（上限 300s）
    - -412 / v_voucher：风控，立即失败（重试无用）
    - 其他业务 code≠0：立即失败
    """
    last = "unknown"
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Referer": REFERER,
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://www.bilibili.com",
                **({"Cookie": cookie} if cookie else {}),
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                ra = (e.headers.get("Retry-After") or "").strip()
                wait = min(int(ra) if ra.isdigit() else 30 * (i + 1), 300)
                time.sleep(wait)
                last = "HTTP 429"
                continue
            if e.code in (500, 502, 503, 504):
                last = f"HTTP {e.code}"
                time.sleep(min(1.5 * (2 ** i), 60))
                continue
            raise BiliError(f"HTTP {e.code}: {url}", code=e.code, kind="network") from e
        except Exception as e:  # noqa: BLE001 —— 网络层什么都可能抛
            last = f"{type(e).__name__}: {e}"
            time.sleep(min(1.5 * (2 ** i), 60))
            continue

        if not isinstance(data, dict) or "code" not in data:
            return data  # 字幕 CDN 直接返回裸 JSON
        code = data.get("code")
        if code == 0:
            return data
        if code == -412 or "v_voucher" in json.dumps(data.get("data") or {}, ensure_ascii=False):
            raise BiliError("被风控（-412 / v_voucher），换出口 IP 或稍后再试", code=-412, kind="risk")
        if code in (-101, -400):
            raise BiliError(f"未登录或参数错误：{data.get('message')}", code=code, kind="auth")
        raise BiliError(f"接口错误 code={code} msg={data.get('message')}", code=code)
    raise BiliError(f"请求失败：{url}（{last}）", kind="network")


def wbi_mix(cookie: str = "") -> str:
    """取 wbi 混淆 key（每次请求前取一次即可，短时间复用）。"""
    nav = get_json(f"{API}/x/web-interface/nav", cookie)
    wbi = (nav.get("data") or {}).get("wbi_img") or {}
    img = (wbi.get("img_url") or "").rsplit("/", 1)[-1].split(".")[0]
    sub = (wbi.get("sub_url") or "").rsplit("/", 1)[-1].split(".")[0]
    if not img or not sub:
        raise BiliError("拿不到 wbi key（nav 接口异常）", kind="api")
    return "".join((img + sub)[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def wbi_query(params: dict, mix: str) -> str:
    """签名后的 query string（已含 wts / w_rid）。"""
    p = dict(params)
    p["wts"] = int(time.time())
    q = urllib.parse.urlencode(sorted(p.items()))
    rid = hashlib.md5((q + mix).encode()).hexdigest()
    return f"{q}&w_rid={rid}"


def nav_info(cookie: str = "") -> dict:
    return (get_json(f"{API}/x/web-interface/nav", cookie).get("data") or {})