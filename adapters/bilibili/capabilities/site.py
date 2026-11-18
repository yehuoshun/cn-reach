# -*- coding: utf-8 -*-
"""L1 站点结构能力：排行榜 / 热门 / UP主信息与投稿 / 番剧影视 / 动态。

全部只读；auth 一律 False —— cookie 传了就用（少踩风控），不传也尽量能出数据。
输出统一成「一眼能看懂」的形状：标题 / 作者 / 链接 / 计数 / 时间（北京时间）。
"""
from __future__ import annotations

import re
import time
from html import unescape
from urllib.parse import quote

from .. import client
from .spec import Capability, param

# 番剧在 api.bilibili.com 上，但路径在 /pgc 下
PGC_PATH = "/pgc/view/web/season"

# 统一按北京时间（UTC+8）渲染，不依赖本机时区 —— 与 adapter.py 的 _bj_time 同款写法。
BJ_OFFSET = 8 * 3600

TAG_RE = re.compile(r"<[^>]+>")

# 空间类接口的风控指纹参数：B站网页端每次请求都会带，缺了容易被 -352。
# 实测（匿名，2026-11）：acc/info 不带 → code=-352 风控校验失败；加上 → code=0。
_DM_PARAMS = {
    "dm_img_list": "[]",
    "dm_img_str": "V2ViR0wgMS4wIChPcGVuR0wgRVMgMi4wIENocm9taXVtKQ",
    "dm_cover_img_str": (
        "QU5HTEUgKE5WSURJQSwgTlZJRElBIEdlRm9yY2UgR1RYIDE2NTAgKE5WSURJQSBHZUZvcmNlIEdUWCAxNjUwKSk"
    ),
    "dm_img_inter": '{"ds":[],"wh":[0,0,0],"of":[0,0,0]}',
}

# 命中这些码就补指纹再试一次：-352/-412 风控、-799 频控、HTTP 412
_RISK_CODES = {-352, -412, -799, 412}

# 动态类型 → 中文短标签（不懂的类型原样回显）
_DYN_KIND = {
    "DYNAMIC_TYPE_AV": "视频",
    "DYNAMIC_TYPE_DRAW": "图文",
    "DYNAMIC_TYPE_WORD": "动态",
    "DYNAMIC_TYPE_ARTICLE": "专栏",
    "DYNAMIC_TYPE_FORWARD": "转发",
    "DYNAMIC_TYPE_LIVE_RCMD": "直播",
    "DYNAMIC_TYPE_PGC": "番剧",
    "DYNAMIC_TYPE_OPUS": "图文",
    "DYNAMIC_TYPE_COMMON_SQUARE": "分享",
    "DYNAMIC_TYPE_MUSIC": "音乐",
    "DYNAMIC_TYPE_COURSES": "课程",
    "DYNAMIC_TYPE_NONE": "动态",
}


# ───────────────────────── 渲染小工具 ─────────────────────────

def _bj_time(ts, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """epoch 秒 → 北京时间字符串（用 gmtime+偏移，不调 localtime，跨平台确定）。"""
    return time.strftime(fmt, time.gmtime(int(ts or 0) + BJ_OFFSET))


def _time(ts, fmt: str = "%Y-%m-%d %H:%M"):
    """带空值保护：没有时间戳就返回 None，别渲染成 1970。"""
    if ts in (None, "", 0, "0"):
        return None
    try:
        return _bj_time(ts, fmt)
    except (TypeError, ValueError):
        return None


def _clip(text):
    """已经是「2024-06-22 20:00:00」这种北京时间字符串时，只留到分钟。

    B站没开播/没数据时会给「0000-00-00 00:00:00」这种占位串，统一当没有。
    """
    s = str(text or "").strip()
    if not s or s.startswith("0000"):
        return None
    return s[:16]


def _short(text, n) -> str | None:
    """超长截断并加省略号（动态正文/标题只给人看个大概）。"""
    s = str(text or "").strip()
    if not s:
        return None
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _fix_url(url):
    """动态里常给 「//www.bilibili.com/...」这种协议相对链接，补成 https。"""
    s = str(url or "").strip()
    if s.startswith("//"):
        return "https:" + s
    if s.startswith("http://"):
        return "https://" + s[len("http://"):]
    return s or None


def _clean(text) -> str:
    """去掉 HTML 标签（标题 / 简介里常带 <em> 之类）。"""
    return unescape(TAG_RE.sub("", str(text or ""))).strip()


def _dur(sec) -> str:
    """秒 → mm:ss（超过 1 小时给 h:mm:ss）。"""
    try:
        s = int(float(sec or 0))
    except (TypeError, ValueError):
        return ""
    h, m, rest = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{rest:02d}" if h else f"{m:02d}:{rest:02d}"


def _num(v):
    """计数统一 int；拿不到就 None（别把字符串当数字算）。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _video_item(v: dict) -> dict:
    """ranking / popular 两条接口的视频对象形状基本一致，统一成一样。"""
    st = v.get("stat") or {}
    owner = v.get("owner") or {}
    bv = v.get("bvid") or ""
    return {
        "id": bv,
        "aid": v.get("aid"),
        "title": _clean(v.get("title")),
        "author": owner.get("name"),
        "mid": owner.get("mid"),
        "url": f"https://www.bilibili.com/video/{bv}" if bv else v.get("short_link_v2"),
        "partition": v.get("tname"),
        "duration": _num(v.get("duration")),
        "duration_hms": _dur(v.get("duration")),
        "pubdate": _time(v.get("pubdate")),
        "stats": {
            "view": _num(st.get("view")),
            "danmaku": _num(st.get("danmaku")),
            "reply": _num(st.get("reply")),
            "favorite": _num(st.get("favorite")),
            "coin": _num(st.get("coin")),
            "share": _num(st.get("share")),
            "like": _num(st.get("like")),
        },
        "cover": v.get("pic"),
    }


# ───────────────────────── 排行榜 / 热门 ─────────────────────────

def _ranking(ad, rid=0, limit=50) -> dict:
    """全站 / 分区排行榜。rid=0 为全站；limit 截断条数。"""
    rid = int(rid or 0)
    data = client.get_json(
        f"{client.API}/x/web-interface/ranking/v2?rid={rid}&type=all", ad.cookie
    ).get("data") or {}
    raw = data.get("list") or []
    if not raw:
        raise client.BiliError(f"排行榜没有数据：rid={rid}（分区号可能不存在）", kind="not_found")
    items = [_video_item(v) for v in raw[: max(1, int(limit or 0))]]
    return {
        "platform": ad.name,
        "rid": rid,
        "count": len(items),
        "note": data.get("note"),
        "items": items,
    }


def _popular(ad, page=1, page_size=20) -> dict:
    """综合热门。"""
    page, page_size = int(page or 1), int(page_size or 20)
    data = client.get_json(
        f"{client.API}/x/web-interface/popular?pn={page}&ps={page_size}", ad.cookie
    ).get("data") or {}
    raw = data.get("list") or []
    if not raw:
        raise client.BiliError("热门列表为空（接口异常或分页越界）", kind="not_found")
    return {
        "platform": ad.name,
        "page": page,
        "page_size": page_size,
        "count": len(raw),
        "no_more": bool(data.get("no_more")),
        "items": [_video_item(v) for v in raw],
    }


# ───────────────────────── UP主 ─────────────────────────

def _space_wbi_get(path: str, params: dict, cookie: str) -> dict:
    """空间接口统一入口：先按最小参数打，被风控就补 dm_* 指纹再打一次。

    B站 space 系列对匿名请求很挑：不带 dm_* 指纹常常 -352 / HTTP 412。
    """
    url = f"{client.API}{path}"
    try:
        return client.wbi_get(url, params, cookie)
    except client.BiliError as e:
        # -101（账号未登录）也值得补指纹再试一次：部分 space 接口只认指纹不认登录态
        retryable = (e.code in _RISK_CODES or e.kind == "risk"
                     or (e.kind == "auth" and e.code in (-101, -400)))
        if not retryable:
            raise
    return client.wbi_get(url, {**params, **_DM_PARAMS}, cookie)


def _up_info(ad, mid) -> dict:
    """UP主名片（wbi 签名；data 就是 UP 主对象）。"""
    mid = int(mid)
    cookie = client.ensure_buvid(ad.cookie or "")
    d = _space_wbi_get("/x/space/wbi/acc/info", {"mid": mid}, cookie).get("data") or {}
    if not d:
        raise client.BiliError(f"UP主不存在或不可见：mid={mid}", kind="not_found")
    return {
        "platform": ad.name,
        "mid": _num(d.get("mid")) or mid,
        "name": d.get("name"),
        "sex": d.get("sex"),
        "sign": d.get("sign"),
        "level": _num(d.get("level")),
        "rank": _num(d.get("rank")),
        "face": d.get("face"),
        "jointime": _time(d.get("jointime"), "%Y-%m-%d"),
        "url": f"https://space.bilibili.com/{mid}",
    }


def _space_video(v: dict) -> dict:
    """space/wbi/arc/search 的 data.list.vlist 单项。"""
    bv = v.get("bvid") or ""
    return {
        "id": bv,
        "aid": v.get("aid"),
        "title": _clean(v.get("title")),
        "author": v.get("author"),
        "url": f"https://www.bilibili.com/video/{bv}" if bv else None,
        "duration": v.get("length"),
        "play": _num(v.get("play")),
        "danmaku": _num(v.get("video_review")),
        "comment": _num(v.get("comment")),
        "pubdate": _time(v.get("created")),
        "desc": _clean(v.get("description"))[:120],
        "cover": v.get("pic"),
    }


def _up_videos(ad, mid, page=1, page_size=20) -> dict:
    """UP主投稿列表（wbi 签名）。

    注意：data.list 是个对象 {slist, tlist, vlist}，真正的投稿在 data.list.vlist，
    总数在 data.page.count —— 别对 data.list 直接 len()。
    """
    mid, page, page_size = int(mid), int(page or 1), int(page_size or 20)
    cookie = client.ensure_buvid(ad.cookie or "")
    data = _space_wbi_get(
        "/x/space/wbi/arc/search",
        {"mid": mid, "pn": page, "ps": page_size, "order": "pubdate"},
        cookie,
    ).get("data") or {}
    if not data:
        raise client.BiliError(f"取不到投稿列表：mid={mid}", kind="not_found")
    lst = data.get("list")
    if isinstance(lst, dict):
        raw = lst.get("vlist") or []
    elif isinstance(lst, list):  # 老版本直接给数组，留着兼容
        raw = lst
    else:
        raw = []
    items = [_space_video(v) for v in raw if isinstance(v, dict)]
    author = raw[0].get("author") if raw and isinstance(raw[0], dict) else None
    return {
        "platform": ad.name,
        "mid": mid,
        "page": page,
        "page_size": page_size,
        "total": _num((data.get("page") or {}).get("count")),
        "count": len(items),
        "author": author,
        "url": f"https://space.bilibili.com/{mid}/video",
        "items": items,
    }


# ───────────────────────── 番剧 / 影视 ─────────────────────────

def _season(ad, season_id=None, ep_id=None) -> dict:
    """番剧 / 影视季信息。

    PGC 接口把数据放在 **result**（不是 data），这一点和 web-interface 系列不一样。
    season_id 与 ep_id 二选一即可。
    """
    if season_id:
        q, sid = f"season_id={int(season_id)}", int(season_id)
    elif ep_id:
        q, sid = f"ep_id={int(ep_id)}", None
    else:
        raise client.BiliError("season 需要 season_id 或 ep_id 之一", kind="api")

    resp = client.get_json(f"{client.API}{PGC_PATH}?{q}", ad.cookie)
    d = resp.get("result") or resp.get("data") or {}
    if not d:
        raise client.BiliError(f"番剧不存在或不可见：{q}", kind="not_found")

    eps = []
    for e in d.get("episodes") or []:
        eid = e.get("ep_id") or e.get("id")
        ms = _num(e.get("duration")) or 0
        eps.append({
            "ep_id": eid,
            "title": e.get("title"),
            "long_title": e.get("long_title"),
            "pubdate": _time(e.get("pub_time")),
            "duration": ms // 1000 if ms else None,
            "duration_hms": _dur(ms // 1000) if ms else None,
            "bvid": e.get("bvid"),
            "url": f"https://www.bilibili.com/bangumi/play/ep{eid}" if eid else None,
        })

    pub = d.get("publish") or {}
    stat = d.get("stat") or {}
    rating = d.get("rating") or {}
    up = d.get("up_info") or {}
    return {
        "platform": ad.name,
        "season_id": d.get("season_id") or sid,
        "title": d.get("season_title") or d.get("title"),
        "type": d.get("type"),
        "total": _num(d.get("total")),
        "status": (d.get("new_ep") or {}).get("desc"),
        "finished": bool(pub.get("is_finish")),
        "pub_time": _clip(pub.get("pub_time")),
        "rating": {"score": rating.get("score"), "count": _num(rating.get("count"))},
        "stats": {
            "view": _num(stat.get("views")),
            "danmaku": _num(stat.get("danmakus")),
            "favorite": _num(stat.get("favorites")),
            "coin": _num(stat.get("coins")),
            "like": _num(stat.get("likes")),
            "reply": _num(stat.get("reply")),
            "share": _num(stat.get("share")),
            "follow": stat.get("follow_text"),
        },
        "areas": [a.get("name") for a in (d.get("areas") or []) if isinstance(a, dict) and a.get("name")],
        "styles": [s.get("name") for s in (d.get("styles") or []) if isinstance(s, dict) and s.get("name")],
        "up": {"mid": up.get("mid"), "name": up.get("uname"), "avatar": up.get("avatar")},
        "url": _fix_url(d.get("link")) or (
            f"https://www.bilibili.com/bangumi/play/ss{sid}" if sid else None),
        "count": len(eps),
        "episodes": eps,
    }


# ───────────────────────── 动态 ─────────────────────────

def _dyn_item(it: dict) -> dict:
    """polymer 动态流单项 → 一个带标题/正文/链接/计数/时间的扁平对象。"""
    mods = it.get("modules") or {}
    author = mods.get("module_author") or {}
    stat = mods.get("module_stat") or {}
    body = mods.get("module_dynamic") or {}
    major = body.get("major") or {}
    desc = (body.get("desc") or {}).get("text") or ""
    kind = it.get("type") or ""
    did = it.get("id_str") or ""

    title, url, bvid, cover = None, None, None, None
    if major.get("archive"):
        a = major["archive"]
        title, bvid, url = a.get("title"), a.get("bvid"), a.get("jump_url")
    elif major.get("article"):
        a = major["article"]
        title = a.get("title")
        url = a.get("jump_url") or (
            f"https://www.bilibili.com/read/cv{a['id']}" if a.get("id") else None)
    elif major.get("opus"):
        o = major["opus"]
        title, url = o.get("title") or o.get("summary"), o.get("jump_url")
    elif major.get("live"):
        live = major["live"]
        title, url, cover = live.get("title"), live.get("jump_url"), live.get("cover")
    elif major.get("pgc"):
        pgc = major["pgc"]
        title, url = pgc.get("title"), pgc.get("jump_url")
    elif major.get("draw"):
        imgs = major["draw"].get("items") or []
        cover = imgs[0].get("src") if imgs and isinstance(imgs[0], dict) else None

    text = _clean(desc)
    out = {
        "id": did,
        "type": kind,
        "kind": _DYN_KIND.get(kind, kind or "动态"),
        "author": author.get("name"),
        "mid": _num(author.get("mid")),
        "time": _time(author.get("pub_ts")) or _clip(author.get("publish_time")),
        "title": _short(_clean(title), 60) if title else _short(text, 60),
        "text": _short(text, 140),
        "url": _fix_url(url) or f"https://t.bilibili.com/{did}",
        "stats": {
            "like": _num((stat.get("like") or {}).get("count")),
            "comment": _num((stat.get("comment") or {}).get("count")),
            "forward": _num((stat.get("forward") or {}).get("count")),
        },
    }
    if bvid:
        out["bvid"] = bvid
    if cover:
        out["cover"] = cover
    return out


def _dynamic(ad, mid, offset="", limit=20) -> dict:
    """UP主动态流（polymer web-dynamic）。

    必须带 buvid3：拿不到指纹（ensure_buvid 失败）就会 -352 风控。
    offset 传上一页返回的 offset 做翻页，空串 = 从头。
    """
    mid, limit = int(mid), int(limit or 20)
    cookie = client.ensure_buvid(ad.cookie or "")
    url = (f"{client.API}/x/polymer/web-dynamic/v1/feed/space?host_mid={mid}"
           f"&offset={quote(str(offset or ''), safe='')}&timezone_offset=-480")
    try:
        resp = client.get_json(url, cookie)
    except client.BiliError as e:
        if e.code in _RISK_CODES or e.kind == "risk":
            raise client.BiliError(
                f"动态接口被风控（code={e.code}）：补 buvid3 cookie 或换出口 IP 再试",
                code=e.code, kind="risk") from e
        raise
    data = resp.get("data") or {}
    items = [_dyn_item(it) for it in (data.get("items") or []) if isinstance(it, dict)]
    items = items[: max(1, limit)]
    return {
        "platform": ad.name,
        "mid": mid,
        "count": len(items),
        "has_more": bool(data.get("has_more")),
        "offset": data.get("offset") or "",
        "url": f"https://space.bilibili.com/{mid}/dynamic",
        "items": items,
    }


CAPABILITIES: list[Capability] = [
    Capability(
        name="ranking",
        summary="排行榜：按分区看全站/分区热榜（rid=0 为全站）",
        handler=_ranking,
        params=(
            param("rid", int, default=0, help="分区号，0=全站"),
            param("limit", int, default=50, help="最多返回条数"),
        ),
        auth=False,
    ),
    Capability(
        name="popular",
        summary="综合热门：全站当前热门视频列表",
        handler=_popular,
        params=(
            param("page", int, default=1, help="页码"),
            param("page_size", int, default=20, help="每页条数"),
        ),
        auth=False,
    ),
    Capability(
        name="up_info",
        summary="UP主信息：昵称 / 签名 / 等级 / 头像 / 主页链接",
        handler=_up_info,
        params=(param("mid", int, required=True, help="UP主 mid"),),
        auth=False,
    ),
    Capability(
        name="up_videos",
        summary="UP主投稿：按发布时间倒序列出该UP主的视频",
        handler=_up_videos,
        params=(
            param("mid", int, required=True, help="UP主 mid"),
            param("page", int, default=1, help="页码"),
            param("page_size", int, default=20, help="每页条数"),
        ),
        auth=False,
    ),
    Capability(
        name="season",
        summary="番剧/影视季：简介、评分、播放数与分集列表",
        handler=_season,
        params=(
            param("season_id", int, help="番剧 ss 号（与 ep_id 二选一）"),
            param("ep_id", int, help="单集 ep 号（与 season_id 二选一）"),
        ),
        auth=False,
    ),
    Capability(
        name="dynamic",
        summary="UP主动态：最新动态（视频/图文/专栏/转发）与互动数",
        handler=_dynamic,
        params=(
            param("mid", int, required=True, help="UP主 mid"),
            param("offset", str, default="", help="翻页游标，空串=第一页"),
            param("limit", int, default=20, help="最多返回条数"),
        ),
        auth=False,
    ),
]
