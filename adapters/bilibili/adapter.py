"""B站适配器：搜索 / 视频详情 / 字幕 / 评论。

只读。不提供任何写操作（收藏、关注、发评论）。
"""
from __future__ import annotations

import re
import time

from . import client
from .client import API, BiliError, get_json, parse_bvid, wbi_mix, wbi_query

TAG_RE = re.compile(r"<[^>]+>")

# 统一按北京时间（UTC+8）渲染，不依赖本机时区 —— Linux / macOS / Windows 结果一致。
BJ_OFFSET = 8 * 3600


def _clean(text: str | None) -> str:
    return TAG_RE.sub("", text or "").strip()


def _ts(sec: float | int | None) -> str:
    s = int(sec or 0)
    return f"{s // 60:02d}:{s % 60:02d}"


def _bj_time(ts: float | int | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """epoch 秒 → 北京时间字符串（用 gmtime+偏移，不调 localtime，跨平台确定）。"""
    return time.strftime(fmt, time.gmtime(int(ts or 0) + BJ_OFFSET))


class BilibiliAdapter:
    name = "bilibili"
    title = "B站 / Bilibili"
    # 后端按优先级排列；目前只有官方 web API + cookie 一条路
    backends = ["web-api"]
    active_backend = "web-api"

    def __init__(self, cookie: str | None = None):
        self.cookie = cookie if cookie is not None else client.load_cookie()

    # ───────────────────────── 体检 ─────────────────────────

    def doctor(self) -> dict:
        """检查 cookie / 登录态 / wbi 是否可用。"""
        out = {
            "platform": self.name,
            "backend": self.active_backend,
            "cookie_file": str(client.cookie_path()),
            "has_cookie": bool(self.cookie),
            "logged_in": False,
            "ok": False,
        }
        try:
            nav = client.nav_info(self.cookie)
            out["logged_in"] = bool(nav.get("isLogin"))
            out["user"] = nav.get("uname")
            out["level"] = (nav.get("level_info") or {}).get("current_level")
            out["wbi"] = bool(client.wbi_mix(self.cookie))
            out["ok"] = True
        except BiliError as e:
            out["error"] = str(e)
            out["kind"] = e.kind
        return out

    # ───────────────────────── 搜索 ─────────────────────────

    def search(self, keyword: str, page: int = 1, page_size: int = 20,
               order: str = "totalrank", limit: int | None = None) -> dict:
        """关键词搜索视频。order: totalrank(综合) / click(播放) / pubdate(最新) / dm(弹幕)。"""
        mix = wbi_mix(self.cookie)
        q = wbi_query({
            "search_type": "video",
            "keyword": keyword,
            "page": page,
            "page_size": page_size,
            "order": order,
        }, mix)
        data = get_json(f"{API}/x/web-interface/wbi/search/type?{q}", self.cookie).get("data") or {}
        raw = data.get("result")
        if not isinstance(raw, list):
            raise BiliError(f"搜索无结果或接口异常：{data.get('message') or data}", kind="not_found")

        items = []
        for v in raw:
            bv = v.get("bvid")
            if not bv:
                continue
            pub = v.get("pubdate") or 0
            items.append({
                "id": bv,
                "title": _clean(v.get("title")),
                "author": v.get("author"),
                "url": f"https://www.bilibili.com/video/{bv}",
                "play": v.get("play"),
                "danmaku": v.get("video_review"),
                "duration": v.get("duration"),
                "pubdate": _bj_time(pub, "%Y-%m-%d") if pub else None,
                "description": _clean(v.get("description"))[:200],
            })
            if limit and len(items) >= limit:
                break
        return {"platform": self.name, "query": keyword, "order": order,
                "count": len(items), "items": items}

    # ───────────────────────── 详情 ─────────────────────────

    def _view(self, target: str) -> dict:
        bvid = parse_bvid(target)
        data = get_json(f"{API}/x/web-interface/view?bvid={bvid}", self.cookie).get("data")
        if not data:
            raise BiliError(f"视频不存在或不可见：{bvid}", kind="not_found")
        return data

    def video(self, target: str) -> dict:
        """按 BV 号 / URL 取视频详情。"""
        d = self._view(target)
        st = d.get("stat") or {}
        return {
            "platform": self.name,
            "id": d["bvid"],
            "aid": d["aid"],
            "cid": d["cid"],
            "title": d["title"],
            "author": d.get("owner", {}).get("name"),
            "desc": d.get("desc", ""),
            "duration": d.get("duration", 0),
            "duration_hms": _ts(d.get("duration")),
            "pubdate": _bj_time(d.get("pubdate")),
            "url": f"https://www.bilibili.com/video/{d['bvid']}",
            "cover": d.get("pic"),
            "pages": [{"cid": p["cid"], "part": p["part"], "duration": p.get("duration")}
                      for p in d.get("pages", [])],
            "stats": {
                "view": st.get("view"), "danmaku": st.get("danmaku"), "reply": st.get("reply"),
                "favorite": st.get("favorite"), "coin": st.get("coin"),
                "share": st.get("share"), "like": st.get("like"),
            },
        }

    # ───────────────────────── 字幕 ─────────────────────────

    def _subtitle_once(self, bvid: str, cid: int) -> tuple[list | None, str | None]:
        """拉一次字幕。必须走 wbi 签名的 player/wbi/v2。

        ⚠️ 旧接口 x/player/v2 不带签名会返回**别的视频**的字幕（interest-radar 实测事故），
        所以这里只用签名接口，拿不到就是拿不到。
        """
        mix = wbi_mix(self.cookie)
        q = wbi_query({"bvid": bvid, "cid": cid}, mix)
        p = get_json(f"{API}/x/player/wbi/v2?{q}", self.cookie)
        subs = (((p.get("data") or {}).get("subtitle") or {}).get("subtitles")) or []
        subs = [s for s in subs if (s.get("subtitle_url") or "").strip()]
        if not subs:
            return None, None
        pick = next((s for s in subs if str(s.get("lan", "")).startswith("zh")), subs[0])
        url = pick.get("subtitle_url") or ""
        if url.startswith("//"):
            url = "https:" + url
        body = get_json(url, self.cookie).get("body") or []
        items = [{"from": b.get("from", 0), "to": b.get("to", 0),
                  "text": re.sub(r"\s+", " ", b.get("content", "")).strip()}
                 for b in body if b.get("content")]
        return items, pick.get("lan")

    def subtitles(self, target: str, cid: int | None = None,
                  max_chars: int | None = None) -> dict:
        """取字幕；没有字幕时兜底返回简介 + 热评，并明确标记不是字幕。"""
        d = self._view(target)
        bvid = d["bvid"]
        use_cid = cid or d["cid"]
        # 多P 时总时长是所有分P 之和，算语速要用「当前这一P」的时长
        page = next((p for p in d.get("pages", []) if p.get("cid") == use_cid), None)
        duration = (page or {}).get("duration") or d.get("duration") or 0

        items, lan = self._subtitle_once(bvid, use_cid)

        base = {"platform": self.name, "id": bvid, "cid": use_cid,
                "title": d["title"], "page_duration": duration,
                "duration": d.get("duration") or 0}

        if not items:
            return {**base, "available": False, "lang": None,
                    "fallback": {"type": "desc+comments", "desc": d.get("desc", ""),
                                 "comments": self.comments(bvid, limit=10)["items"]}}

        text = "".join(i["text"] for i in items)
        chars = len(text)
        rate = round(chars / duration, 2) if duration else 0
        suspect = bool(duration) and (rate > 15 or rate < 0.8)
        reason = None
        if suspect:
            # 语速不合理 → 再拉一次，若两次不一致即判定字幕不可信
            again, _ = self._subtitle_once(bvid, use_cid)
            if again != items:
                reason = f"语速异常 {rate} 字/秒，且两次结果不一致 —— 字幕不可信"

        return {
            **base,
            "available": True,
            "lang": lan,
            "count": len(items),
            "chars": chars,
            "rate": rate,
            "suspect": bool(reason),
            "suspect_reason": reason,
            "text": text[:max_chars] if max_chars else text,
            "truncated": bool(max_chars and chars > max_chars),
            "items": [{"from": i["from"], "to": i["to"], "ts": _ts(i["from"]),
                       "text": i["text"]} for i in items],
        }

    # ───────────────────────── 评论 ─────────────────────────

    def comments(self, target: str, limit: int = 20, sort: str = "hot") -> dict:
        """热评。sort: hot(按热度) / time(按时间)。"""
        d = self._view(target)
        aid = d["aid"]
        ps = max(1, min(limit, 49))
        order = 2 if sort == "hot" else 0
        url = f"{API}/x/v2/reply?type=1&oid={aid}&sort={order}&ps={ps}&nested=0"
        data = get_json(url, self.cookie).get("data") or {}
        items = []
        for r in (data.get("replies") or [])[:limit]:
            items.append({
                "user": (r.get("member") or {}).get("uname"),
                "like": r.get("like", 0),
                "content": (r.get("content") or {}).get("message", ""),
                "time": _bj_time(r.get("ctime")),
            })
        return {"platform": self.name, "id": d["bvid"], "sort": sort,
                "count": len(items), "items": items}