# -*- coding: utf-8 -*-
"""L2 登录态能力：我的信息 / 观看历史 / 稍后再看 / 我的收藏夹 / 关注 / 动态时间线 / 追番。

全部需要登录态（cookie 里要有 SESSDATA）。没有 cookie 时由 BilibiliAdapter.call()
直接抛 auth 错，一个请求都不会发出去；cookie 过期则由 nav 返回 isLogin=False 兜住。

需要 mid 的列表（收藏夹 / 关注 / 追番）统一从 nav 里取，不让用户传。
端点路径、签名方式只留在本文件内，不写进 summary / catalog（对外只暴露能力）。
"""
from __future__ import annotations

import time
import urllib.parse

from .. import client
from .spec import Capability, param

PLATFORM = "bilibili"
BJ_OFFSET = 8 * 3600

MAX_PAGE_SIZE = 50      # 单页最多 50 条，再大容易触发限流
MAX_PAGE_WALK = 20      # 游标类接口按页翻时最多走 20 跳，防止误传大页码打爆上游


# ───────────────────────── 小工具 ─────────────────────────

def _bj_time(ts, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """epoch 秒 → 北京时间字符串（gmtime + 偏移，不依赖本机时区，三平台一致）。"""
    try:
        sec = int(ts or 0)
    except (TypeError, ValueError):
        sec = 0
    return time.strftime(fmt, time.gmtime(sec + BJ_OFFSET))


def _q(**params) -> str:
    """拼 query：None 一律丢掉，避免出现 max=None 这种脏参数。"""
    return urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})


def _unwrap(resp) -> dict:
    """client 层返回的是完整信封 {code, data, message, ttl}，取 data；空值统一给 {}。"""
    return (resp or {}).get("data") or {}


def _get(url: str, cookie: str) -> dict:
    return _unwrap(client.get_json(url, cookie))


def _clamp(val, lo: int, hi: int, default: int) -> int:
    """把用户传的页码 / 条数夹到安全区间，脏值退回默认。"""
    try:
        num = int(val)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, num))


def _space_url(mid) -> str | None:
    return f"https://space.bilibili.com/{mid}" if mid else None


def _video_url(bvid) -> str | None:
    return f"https://www.bilibili.com/video/{bvid}" if bvid else None


def _nav(ad) -> dict:
    """取登录信息；未登录直接报 auth 错（cookie 失效时最常走到这里）。"""
    nav = client.nav_info(ad.cookie)
    if not nav.get("isLogin"):
        raise client.BiliError("未登录（cookie 缺失或已失效）", code=-101, kind="auth")
    return nav


def _mid(ad) -> int:
    """当前登录用户 mid —— 个人列表类能力的必备前置。"""
    mid = _nav(ad).get("mid")
    if not mid:
        raise client.BiliError("登录信息里没有 mid，取不到个人列表", kind="api")
    return int(mid)


# ───────────────────────── 1. me ─────────────────────────

def _me(ad, **_) -> dict:
    """我的登录信息。未登录不是错误，用 logged_in=False 如实返回。"""
    nav = client.nav_info(ad.cookie)
    level_info = nav.get("level_info") or {}
    vip = nav.get("vip") or {}
    label = vip.get("label")
    return {
        "platform": PLATFORM,
        "logged_in": bool(nav.get("isLogin")),
        "mid": nav.get("mid"),
        "name": nav.get("uname"),
        "level": level_info.get("current_level"),
        "exp": level_info.get("current_exp"),
        "coins": nav.get("money"),
        "vip": bool(vip.get("status")),
        "vip_label": label.get("text") if isinstance(label, dict) else None,
        "avatar": nav.get("face"),
    }


# ───────────────────────── 2. history ─────────────────────────

def _history(ad, page: int = 1, page_size: int = 20, **_) -> dict:
    """观看历史。上游是游标接口：取第 N 页就逐页走游标（最多 MAX_PAGE_WALK 跳）。"""
    ps = _clamp(page_size, 1, MAX_PAGE_SIZE, 20)
    pn = _clamp(page, 1, MAX_PAGE_WALK, 1)
    max_aid, view_at = 0, 0
    payload: dict = {}
    for _ in range(pn):
        url = (f"{client.API}/x/web-interface/history/cursor?"
               + _q(max=max_aid, view_at=view_at, business="", ps=ps, type="all"))
        payload = _get(url, ad.cookie)
        cursor = payload.get("cursor") or {}
        max_aid = cursor.get("max") or 0
        view_at = cursor.get("view_at") or 0
        if not payload.get("list"):     # 走到头了就不必再翻
            break

    items = []
    for it in payload.get("list") or []:
        hist = it.get("history") or {}
        bvid = hist.get("bvid") or it.get("bvid")
        oid = hist.get("oid") or it.get("aid")
        items.append({
            "id": bvid or (str(oid) if oid else None),
            "title": it.get("title") or "",
            "author": it.get("author_name"),
            "author_mid": it.get("author_mid"),
            "url": _video_url(bvid),
            "business": hist.get("business"),
            "progress": it.get("progress"),
            "current": it.get("current"),
            "duration": it.get("duration"),
            "viewed_at": _bj_time(it.get("view_at")),
        })
    return {"platform": PLATFORM, "page": pn, "page_size": ps,
            "count": len(items), "items": items}


# ───────────────────────── 3. toview ─────────────────────────

def _toview(ad, **_) -> dict:
    """稍后再看。"""
    data = _get(f"{client.API}/x/v2/history/toview", ad.cookie)
    items = []
    for v in data.get("list") or []:
        bvid = v.get("bvid")
        aid = v.get("aid")
        items.append({
            "id": bvid or (str(aid) if aid else None),
            "title": v.get("title") or "",
            "author": (v.get("owner") or {}).get("name"),
            "url": _video_url(bvid),
            "duration": v.get("duration"),
            "progress": v.get("progress"),
            "added_at": _bj_time(v.get("add_at")),
        })
    return {"platform": PLATFORM, "count": len(items),
            "total": data.get("count"), "items": items}


# ───────────────────────── 4. my_folders ─────────────────────────

def _my_folders(ad, **_) -> dict:
    """我的收藏夹列表（含默认收藏夹）。id 用 v3 口径的 id，fid 只做兜底。"""
    mid = _mid(ad)
    data = _get(f"{client.API}/x/v3/fav/folder/created/list-all?up_mid={mid}", ad.cookie)
    items = []
    for f in data.get("list") or []:
        fid = f.get("id") or f.get("fid")
        attr = f.get("attr")
        items.append({
            "id": fid,
            "fid": f.get("fid"),
            "title": f.get("title"),
            "media_count": f.get("media_count"),
            "private": bool(attr & 1) if isinstance(attr, int) else None,
            "url": (f"https://space.bilibili.com/{mid}/favlist?fid={fid}" if fid else None),
        })
    return {"platform": PLATFORM, "mid": mid, "count": len(items),
            "total": data.get("count"), "items": items}


# ───────────────────────── 5. followings ─────────────────────────

def _relation(ad, path: str, page, page_size) -> dict:
    """关注列表（followings）的解析。"""
    mid = _mid(ad)
    pn = _clamp(page, 1, 1000, 1)
    ps = _clamp(page_size, 1, MAX_PAGE_SIZE, 20)
    url = (f"{client.API}{path}?"
           + _q(vmid=mid, pn=pn, ps=ps, order="desc", order_type="attention"))
    data = _get(url, ad.cookie)
    items = [{
        "mid": u.get("mid"),
        "name": u.get("uname"),
        "sign": u.get("sign"),
        "url": _space_url(u.get("mid")),
    } for u in data.get("list") or []]
    return {"platform": PLATFORM, "mid": mid, "page": pn, "page_size": ps,
            "count": len(items), "total": data.get("total"), "items": items}


def _followings(ad, page: int = 1, page_size: int = 20, **_) -> dict:
    """我关注的人（按关注时间倒序）。"""
    return _relation(ad, "/x/relation/followings", page, page_size)


# ───────────────────────── 7. my_dynamic ─────────────────────────

def _dynamic_item(it: dict) -> dict:
    """把一条动态压成稳定字段；非视频动态的正文可能为空，不编造。"""
    modules = it.get("modules") or {}
    author = modules.get("module_author") or {}
    dynamic = modules.get("module_dynamic") or {}
    desc = (dynamic.get("desc") or {}).get("text") or ""
    dtype = it.get("type")
    author_mid = author.get("mid")
    text = desc
    url = _space_url(author_mid)
    if dtype == "DYNAMIC_TYPE_AV":
        archive = (dynamic.get("major") or {}).get("archive") or {}
        bvid = archive.get("bvid")
        url = _video_url(bvid) or url
        text = text or (archive.get("title") or "")
    return {
        "id": it.get("id_str"),
        "type": dtype,
        "author": author.get("name"),
        "author_mid": author_mid,
        "published_at": _bj_time(author.get("pub_ts")),
        "text": text[:200],
        "url": url,
    }


def _my_dynamic(ad, offset: str = "", limit: int = 20, **_) -> dict:
    """动态时间线。必须走 wbi 签名；翻页把返回的 offset 原样传回来。"""
    lim = _clamp(limit, 1, MAX_PAGE_SIZE, 20)
    resp = client.wbi_get(
        f"{client.API}/x/polymer/web-dynamic/v1/feed/all",
        {"type": "all", "page": 1, "offset": offset or "", "limit": lim},
        ad.cookie,
    )
    data = _unwrap(resp)
    items = [_dynamic_item(it) for it in data.get("items") or []]
    return {"platform": PLATFORM, "count": len(items),
            "has_more": bool(data.get("has_more")), "offset": data.get("offset"),
            "update_num": data.get("update_num"), "items": items}


# ───────────────────────── 8. my_bangumi ─────────────────────────

def _my_bangumi(ad, page: int = 1, page_size: int = 20, **_) -> dict:
    """我的追番列表。空列表是正常状态（没追番），不当错误。"""
    mid = _mid(ad)
    pn = _clamp(page, 1, 1000, 1)
    ps = _clamp(page_size, 1, MAX_PAGE_SIZE, 20)
    url = (f"{client.API}/x/space/bangumi/follow/list?"
           + _q(type=1, follow_status=0, vmid=mid, pn=pn, ps=ps, ts=int(time.time())))
    data = _get(url, ad.cookie)
    items = []
    for s in data.get("list") or []:
        season_id = s.get("season_id")
        items.append({
            "season_id": season_id,
            "media_id": s.get("media_id"),
            "title": s.get("title"),
            "type": s.get("season_type_name"),
            "cover": s.get("cover"),
            "progress": (s.get("new_ep") or {}).get("index_show"),
            "total": s.get("total_count"),
            "status": s.get("follow_status"),
            "url": s.get("url") or (
                f"https://www.bilibili.com/bangumi/play/ss{season_id}" if season_id else None),
        })
    return {"platform": PLATFORM, "mid": mid, "page": pn, "page_size": ps,
            "count": len(items), "total": data.get("total"), "items": items}


# ───────────────────────── 能力声明 ─────────────────────────

CAPABILITIES: list[Capability] = [
    Capability(
        name="me",
        summary="我的登录信息：用户名 / 等级 / 是否登录",
        handler=_me,
        params=(),
        auth=True,
    ),
    Capability(
        name="history",
        summary="我的观看历史（按页取，每页默认 20 条）",
        handler=_history,
        params=(
            param("page", int, default=1, help="页码，从 1 开始"),
            param("page_size", int, default=20, help="每页条数（上限 50）"),
        ),
        auth=True,
    ),
    Capability(
        name="toview",
        summary="我的稍后再看列表",
        handler=_toview,
        params=(),
        auth=True,
    ),
    Capability(
        name="my_folders",
        summary="我的收藏夹列表（含默认收藏夹）",
        handler=_my_folders,
        params=(),
        auth=True,
    ),
    Capability(
        name="followings",
        summary="我关注的人（按关注时间倒序）",
        handler=_followings,
        params=(
            param("page", int, default=1, help="页码，从 1 开始"),
            param("page_size", int, default=20, help="每页条数（上限 50）"),
        ),
        auth=True,
    ),
    Capability(
        name="my_dynamic",
        summary="我的动态时间线（游标翻页）",
        handler=_my_dynamic,
        params=(
            param("offset", str, default="", help="翻页游标，取上一页返回的 offset"),
            param("limit", int, default=20, help="每页条数（上限 50）"),
        ),
        auth=True,
    ),
    Capability(
        name="my_bangumi",
        summary="我的追番列表",
        handler=_my_bangumi,
        params=(
            param("page", int, default=1, help="页码，从 1 开始"),
            param("page_size", int, default=20, help="每页条数（上限 50）"),
        ),
        auth=True,
    ),
]
