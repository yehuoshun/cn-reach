# -*- coding: utf-8 -*-
"""L1 内容取数能力：搜索 / 视频详情 / 分P / 字幕 / 评论 / 子评论 / 相关推荐。

分工：
  - search / video / subtitle / comments 的实现在 adapter.py，本模块只做能力契约包装，不复制实现；
  - pages / replies / related 在本模块直接用 client 取数；
  - 每个 handler 都返回带 "platform": "bilibili" 的 dict，出错统一抛 client.BiliError；
  - cookie 一律取 ad.cookie（BilibiliAdapter 已加载，支持构造时注入），不在这里 load_cookie。
"""
from __future__ import annotations

from .. import client
from ..adapter import _bj_time, _clean, _ts
from .spec import Capability, param

PLATFORM = "bilibili"


# ───────────────────────── 小工具 ─────────────────────────


def _to_int(val) -> int | None:
    """'1234' / 1234 → 1234；空值、布尔、非数字 → None。"""
    if val is None or val == "" or isinstance(val, bool):
        return None
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return None


def _ensure_platform(out: dict) -> dict:
    """能力返回体必须带 platform；适配器已经带上的就不覆盖。"""
    if isinstance(out, dict):
        out.setdefault("platform", PLATFORM)
    return out


def _view(ad, target: str) -> dict:
    """视频详情（走 adapter，避免和 adapter 里的归一化逻辑各写一套）。"""
    return ad.video(target) or {}


# ───────────────────────── 包装 adapter 的 4 个能力 ─────────────────────────


def _search(ad, keyword: str, page: int = 1, page_size: int = 20,
            order: str = "totalrank", limit: int = 20) -> dict:
    """关键词搜索视频（实现在 adapter.search）。"""
    return _ensure_platform(ad.search(keyword, page=page, page_size=page_size,
                                      order=order, limit=limit))


def _video(ad, target: str) -> dict:
    """视频详情（实现在 adapter.video）。"""
    return _ensure_platform(ad.video(target))


def _subtitle(ad, target: str, cid: int | None = None,
              max_chars: int | None = None) -> dict:
    """字幕（实现在 adapter.subtitles；无字幕时适配器自带简介+热评兜底）。"""
    return _ensure_platform(ad.subtitles(target, cid=cid, max_chars=max_chars))


def _comments(ad, target: str, limit: int = 20, sort: str = "hot") -> dict:
    """热评 / 最新评论（实现在 adapter.comments）。"""
    return _ensure_platform(ad.comments(target, limit=limit, sort=sort))


# ───────────────────────── 分P ─────────────────────────


def _pages(ad, target: str) -> dict:
    """分P 列表：cid / 标题 / 时长。cid 是字幕与评论定位的唯一入口。"""
    v = _view(ad, target)
    items = []
    for i, p in enumerate(v.get("pages") or [], start=1):
        items.append({
            "index": i,
            "cid": _to_int(p.get("cid")),
            "part": p.get("part") or "",
            "duration": _to_int(p.get("duration")),
            "duration_hms": _ts(p.get("duration")),
        })
    if not items and _to_int(v.get("cid")):      # 兜底：pages 缺失时按单P 处理
        items = [{"index": 1, "cid": _to_int(v.get("cid")), "part": v.get("title") or "",
                  "duration": _to_int(v.get("duration")),
                  "duration_hms": _ts(v.get("duration"))}]
    return {"platform": PLATFORM, "id": v.get("id"), "aid": v.get("aid"),
            "count": len(items), "current": items[0]["cid"] if items else None,
            "items": items}


# ───────────────────────── 评论 / 子评论 ─────────────────────────


def _reply_item(r: dict) -> dict:
    """单条评论归一（主评论和楼中楼字段一致）。"""
    c = r.get("content") or {}
    m = r.get("member") or {}
    return {
        "rpid": r.get("rpid"),
        "user": m.get("uname"),
        "mid": _to_int(r.get("mid")),
        "like": _to_int(r.get("like")) or 0,
        "reply_count": _to_int(r.get("rcount")) or 0,
        "content": c.get("message", ""),
        "time": _bj_time(r.get("ctime")),
        "parent": r.get("parent"),
        "root": r.get("root"),
    }


def _replies(ad, target: str, root: int | None = None, limit: int = 20) -> dict:
    """楼中楼子评论。root 不传时自动挑一条有回复的热评展开。"""
    v = _view(ad, target)
    aid = _to_int(v.get("aid"))
    bvid = v.get("id")
    if not aid:
        raise client.BiliError(f"取不到 aid：{target}", kind="not_found")
    root_id = _to_int(root)
    picked = None
    if not root_id:
        data = client.get_json(
            f"{client.API}/x/v2/reply?type=1&oid={aid}&sort=2&ps=20&nested=0&pn=1",
            ad.cookie).get("data") or {}
        cands = data.get("replies") or []
        cands = [c for c in cands if (_to_int(c.get("rcount")) or 0) > 0] or cands
        if not cands:
            raise client.BiliError(f"评论区为空，没有楼中楼可展开：{bvid}", kind="not_found")
        picked = cands[0]
        root_id = _to_int(picked.get("rpid"))
        if not root_id:
            raise client.BiliError(f"没取到主评论 rpid：{bvid}", kind="not_found")

    limit = max(1, _to_int(limit) or 20)
    items: list[dict] = []
    page_no, total = 1, None
    while len(items) < limit and page_no <= 5:
        url = (f"{client.API}/x/v2/reply/reply?type=1&oid={aid}&root={root_id}"
               f"&ps=20&pn={page_no}")
        d = client.get_json(url, ad.cookie).get("data") or {}
        batch = d.get("replies") or []
        if not batch:
            break
        for r in batch:
            if len(items) >= limit:
                break
            items.append(_reply_item(r))
        total = _to_int((d.get("page") or {}).get("count")) or total
        if total is not None and len(items) >= total:
            break
        page_no += 1

    out = {"platform": PLATFORM, "id": bvid, "aid": aid, "root": root_id,
           "total": total, "count": len(items), "items": items}
    if picked is not None:
        out["root_comment"] = _reply_item(picked)
    return out


# ───────────────────────── 相关推荐 ─────────────────────────


def _related(ad, target: str, limit: int = 20) -> dict:
    """相关推荐视频。"""
    bvid = client.parse_bvid(target)
    data = client.get_json(
        f"{client.API}/x/web-interface/archive/related?bvid={bvid}", ad.cookie).get("data")
    if not isinstance(data, list) or not data:
        raise client.BiliError(f"没有相关推荐（视频不存在或接口异常）：{bvid}", kind="not_found")

    limit = max(0, _to_int(limit) or 20)
    items = []
    for v in data[:limit]:
        st = v.get("stat") or {}
        owner = v.get("owner") or {}
        pub = _to_int(v.get("pubdate"))
        items.append({
            "id": v.get("bvid"),
            "title": _clean(v.get("title")),
            "author": owner.get("name"),
            "url": f"https://www.bilibili.com/video/{v.get('bvid')}",
            "play": _to_int(st.get("view")),
            "danmaku": _to_int(st.get("danmaku")),
            "like": _to_int(st.get("like")),
            "duration": _to_int(v.get("duration")),
            "duration_hms": _ts(v.get("duration")),
            "pubdate": _bj_time(pub, "%Y-%m-%d") if pub else None,
        })
    return {"platform": PLATFORM, "id": bvid, "count": len(items), "items": items}


# ───────────────────────── 能力注册表 ─────────────────────────

CAPABILITIES: list[Capability] = [
    Capability(
        name="search",
        summary="按关键词搜索视频，可排序翻页",
        handler=_search,
        params=(
            param("keyword", str, required=True, help="关键词"),
            param("page", int, default=1, help="页码，从 1 开始"),
            param("page_size", int, default=20, help="每页条数"),
            param("order", str, default="totalrank",
                  help="排序：totalrank(综合) / click(播放) / pubdate(最新) / dm(弹幕)"),
            param("limit", int, default=20, help="最多返回条数"),
        ),
    ),
    Capability(
        name="video",
        summary="视频详情（标题/UP主/时长/统计/分P）",
        handler=_video,
        params=(param("target", str, required=True, help="BV 号或视频 URL"),),
    ),
    Capability(
        name="subtitle",
        summary="取字幕；没有字幕时兜底返回简介和热评",
        handler=_subtitle,
        params=(
            param("target", str, required=True, help="BV 号或视频 URL"),
            param("cid", int, default=None, help="分P 的 cid，默认第一P"),
            param("max_chars", int, default=None, help="正文最多返回多少字符"),
        ),
    ),
    Capability(
        name="comments",
        summary="取评论（热评或最新）",
        handler=_comments,
        params=(
            param("target", str, required=True, help="BV 号或视频 URL"),
            param("limit", int, default=20, help="最多返回条数"),
            param("sort", str, default="hot", help="排序：hot(热度) / time(时间)"),
        ),
    ),
    Capability(
        name="pages",
        summary="分P 列表（cid / 标题 / 时长）",
        handler=_pages,
        params=(param("target", str, required=True, help="BV 号或视频 URL"),),
    ),
    Capability(
        name="replies",
        summary="楼中楼子评论（不给 root 时自动挑一条有回复的热评）",
        handler=_replies,
        params=(
            param("target", str, required=True, help="BV 号或视频 URL"),
            param("root", int, default=None, help="主评论 rpid"),
            param("limit", int, default=20, help="最多返回条数"),
        ),
    ),
    Capability(
        name="related",
        summary="相关推荐视频",
        handler=_related,
        params=(
            param("target", str, required=True, help="BV 号或视频 URL"),
            param("limit", int, default=20, help="最多返回条数"),
        ),
    ),
]
