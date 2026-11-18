# -*- coding: utf-8 -*-
"""L1 内容取数能力：搜索 / 视频详情 / 分P / 播放地址 / 字幕 / 评论 / 子评论 / 弹幕 / 相关推荐 / 在线人数。

分工：
  - search / video / subtitle / comments 的实现在 adapter.py，本模块只做能力契约包装，不复制实现；
  - pages / playurl / replies / danmaku / related / online 在本模块直接用 client 取数；
  - 每个 handler 都返回带 "platform": "bilibili" 的 dict，出错统一抛 client.BiliError；
  - cookie 一律取 ad.cookie（BilibiliAdapter 已加载，支持构造时注入），不在这里 load_cookie。
"""
from __future__ import annotations

import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zlib

from .. import client, pb
from ..adapter import _bj_time, _clean, _ts
from .spec import Capability, param

PLATFORM = "bilibili"

# playurl 的 fnval 位掩码：dash + 4K + HDR + 杜比音频 + 8K（B站前端同款）
PLAYURL_FNVAL = 4048

# 播放地址带时效性签名，别缓存复用
URL_EXPIRES_HINT = "播放地址带时效性签名，可能几分钟后失效，不要缓存复用"


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


def _cid_of(ad, target: str, cid=None) -> int:
    """cid 优先用参数；没给就回落到视频详情的第一P（会多打一次 view）。"""
    n = _to_int(cid)
    if n:
        return n
    view = _view(ad, target)
    pages = view.get("pages") or []
    n = _to_int(view.get("cid")) or (_to_int(pages[0].get("cid")) if pages else None)
    if not n:
        raise client.BiliError(f"取不到 cid（视频不存在或没有分P）：{target}", kind="not_found")
    return n


def _get_bytes(url: str, cookie: str = "") -> bytes:
    """取二进制响应（弹幕 protobuf / XML 解析用）。

    client 只提供 get_json（内部 json.loads），而 seg.so 返回的是 protobuf 裸字节、
    list.so 返回的是 deflate 压缩的 XML，两个都过不了 JSON 解析，所以这里保留一个
    最小取数实现：复用 client 的 UA / Referer，错误归一成 BiliError。
    若 client 之后补上 get_bytes，自动改用它（见 getattr）。
    """
    fn = getattr(client, "get_bytes", None)
    if callable(fn):
        return fn(url, cookie)
    req = urllib.request.Request(url, headers={
        "User-Agent": client.UA,
        "Referer": client.REFERER,
        "Origin": "https://www.bilibili.com",
        **({"Cookie": cookie} if cookie else {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        # 实测：分段超出范围 / 该段没有弹幕时返回 304，不是错误
        if e.code in (204, 304):
            return b""
        if e.code == 412:
            raise client.BiliError("弹幕接口被风控（412）", code=412, kind="risk") from e
        raise client.BiliError(f"弹幕接口 HTTP {e.code}", code=e.code, kind="network") from e
    except Exception as e:  # noqa: BLE001 —— 网络层什么都可能抛
        raise client.BiliError(f"弹幕接口请求失败：{type(e).__name__}: {e}", kind="network") from e


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
    """分P 列表：cid / 标题 / 时长。cid 是播放、弹幕、字幕的唯一入口。"""
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


# ───────────────────────── 播放地址 ─────────────────────────


def _trim_stream(s: dict) -> dict:
    """dash video 一条流只留必要字段 —— 原始条目带好几条超长 URL，整段吐出来没人用。"""
    return {
        "id": _to_int(s.get("id")),
        "height": _to_int(s.get("height")),
        "width": _to_int(s.get("width")),
        "bandwidth": _to_int(s.get("bandwidth")),
        "codecs": s.get("codecs"),
        "codecid": _to_int(s.get("codecid")),
        "base_url": s.get("baseUrl") or s.get("base_url"),
    }


def _trim_audio(a: dict) -> dict:
    return {
        "id": _to_int(a.get("id")),
        "bandwidth": _to_int(a.get("bandwidth")),
        "codecs": a.get("codecs"),
        "base_url": a.get("baseUrl") or a.get("base_url"),
    }


def _dedupe_streams(videos: list[dict], limit: int) -> list[dict]:
    """同一 (codecid, height) 常有多个码率：留带宽最大的那条，按清晰度从高到低截前 limit 档。"""
    best: dict[tuple, dict] = {}
    for s in videos:
        key = (_to_int(s.get("codecid")), _to_int(s.get("height")))
        cur = best.get(key)
        if cur is None or (_to_int(s.get("bandwidth")) or 0) > (_to_int(cur.get("bandwidth")) or 0):
            best[key] = s
    return [_trim_stream(s) for s in list(best.values())[:max(1, limit)]]


def _playurl(ad, target: str, cid: int | None = None, quality: int | None = None,
             limit: int = 5, durl: bool = False) -> dict:
    """播放地址：默认走签名端点 + fnval=4048 拿 dash，输出裁剪到前几档清晰度。

    清晰度受登录态影响：带 cookie 才给到 1080P+，未登录通常只到 480P。
    durl=True 时降级成不带 fnval 的老接口（flv/mp4 分段直链）。
    """
    bvid = client.parse_bvid(target)
    use_cid = _cid_of(ad, target, cid)
    qn = _to_int(quality) or 80
    limit = max(1, _to_int(limit) or 5)
    base = {"bvid": bvid, "cid": use_cid, "qn": qn, "fnver": 0, "fourk": 1}

    if durl:
        url = (f"{client.API}/x/player/playurl?bvid={bvid}&cid={use_cid}"
               f"&qn={qn}&fnver=0&fourk=1")
        data = client.get_json(url, ad.cookie).get("data") or {}
    else:
        try:
            data = client.wbi_get(f"{client.API}/x/player/wbi/playurl",
                                  {**base, "fnval": PLAYURL_FNVAL}, ad.cookie).get("data") or {}
        except client.BiliError as e:
            if e.kind != "auth":
                raise
            # 实测：没有登录态时签名端点直接 -101「账号未登录」，
            # 未签名端点仍能返回 dash（只是清晰度受限），降级过一次而不是直接失败。
            url = (f"{client.API}/x/player/playurl?bvid={bvid}&cid={use_cid}"
                   f"&qn={qn}&fnval={PLAYURL_FNVAL}&fnver=0&fourk=1")
            data = client.get_json(url, ad.cookie).get("data") or {}

    acc_q = data.get("accept_quality") or []
    acc_d = data.get("accept_description") or []
    name_of = {q: acc_d[i] for i, q in enumerate(acc_q) if i < len(acc_d)}
    dash = data.get("dash") or {}
    videos = dash.get("video") or []
    audios = dash.get("audio") or []
    durls = data.get("durl") or []

    out = {
        "platform": PLATFORM, "id": bvid, "cid": use_cid,
        "is_dash": bool(videos),
        "quality": _to_int(data.get("quality")),
        "quality_name": name_of.get(data.get("quality")),
        "quality_list": [{"id": _to_int(q), "name": name_of.get(q),
                          "default": q == data.get("quality")} for q in acc_q],
        "dash": {
            "video": _dedupe_streams(videos, limit),
            "audio": [_trim_audio(a) for a in audios[:2]],
        },
        "durl": [{"order": _to_int(d.get("order")), "size": _to_int(d.get("size")),
                  "url": d.get("url")} for d in durls[:limit]],
        "url_expires_hint": URL_EXPIRES_HINT,
    }
    if not out["is_dash"] and not out["durl"]:
        raise client.BiliError(
            f"播放地址为空（可能付费 / 地区限制 / 需要登录）：{bvid} cid={use_cid} "
            f"{data.get('message') or ''}".strip(), kind="api")
    if len(videos) > limit:
        out["note"] = (f"dash 原始 {len(videos)} 条流，已按 codecid+height 去重后截前 {limit} 档；"
                       f"要别的清晰度用 quality 指定 quality_list 里的 id")
    return out


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


# ───────────────────────── 弹幕 ─────────────────────────
#
# 字段号是拿真实响应（/x/v2/dm/wbi/web/seg.so，application/octet-stream）逐条核过的：
#   {1: 2234825639763770880, 2: 19367, 3: 1, 4: 25, 5: 16777215, 6: "259af544",
#    7: "这北方和黑岩也不是一般人啊，正常人选机缘…", 8: 1793503176, 9: 10,
#    12: "2234825639763770880", 13: 1048576, 20: "0", 21: "0", 25: 1, 26: <cid>, 27: 1}
# 语义：2=progress(毫秒) 3=mode 5=color 7=content 8=ctime，其余（13/20/21/22/25/26/27）不猜。
DM_PROGRESS, DM_MODE, DM_COLOR = 2, 3, 5
DM_FONT, DM_HASH, DM_TEXT, DM_CTIME = 4, 6, 7, 8
DM_ID, DM_WEIGHT, DM_ID_STR = 1, 9, 12
# 已语义化的字段号；剩下的按原始字段号挂在 item["raw"] 里，不硬编语义
DM_MAPPED = {DM_ID, DM_PROGRESS, DM_MODE, DM_FONT, DM_COLOR, DM_HASH, DM_TEXT,
             DM_CTIME, DM_WEIGHT, DM_ID_STR}


def _color_hex(val) -> tuple[str | None, int | None]:
    """'#FFFFFF' + 十进制颜色值；非整数返回 (None, None)。"""
    n = _to_int(val)
    return (f"#{n:06X}", n) if n is not None else (None, None)


def _dm_item(raw: dict) -> dict:
    """protobuf 解出的一条弹幕 → 对外字段（语义字段 + 未核实字段的原字段号）。"""
    ms = _to_int(raw.get(DM_PROGRESS)) or 0
    color, color_dec = _color_hex(raw.get(DM_COLOR))
    ctime = _to_int(raw.get(DM_CTIME))
    item = {
        "text": raw.get(DM_TEXT) if isinstance(raw.get(DM_TEXT), str) else "",
        "time_ms": ms,
        "time_sec": round(ms / 1000, 3),
        "time_hms": _ts(ms / 1000),
        "mode": _to_int(raw.get(DM_MODE)),
        "fontsize": _to_int(raw.get(DM_FONT)),
        "color": color,
        "color_dec": color_dec,
        "ctime": ctime,
        "time": _bj_time(ctime),
        "mid_hash": raw.get(DM_HASH) if isinstance(raw.get(DM_HASH), str) else None,
        "id": raw.get(DM_ID_STR) or raw.get(DM_ID),
        "weight": _to_int(raw.get(DM_WEIGHT)),
    }
    extra = {}
    for k, v in raw.items():
        if k in DM_MAPPED:
            continue
        if isinstance(v, (bytes, bytearray)):
            v = v.decode("utf-8", errors="replace")
        if isinstance(v, str) and len(v) > 120:
            v = v[:120]
        extra[str(k)] = v
    if extra:
        item["raw"] = extra            # 未核实字段保留原字段号，不硬编语义
    return item


def _xml_root(blob: bytes):
    """弹幕 XML 解包：接口声明 text/xml，实测 body 是 raw deflate（无 zlib 头）。"""
    if not blob:
        return ET.fromstring(b"<i></i>")
    try:
        return ET.fromstring(blob)
    except ET.ParseError:
        pass
    for fn in (lambda b: zlib.decompress(b, -15),
               zlib.decompress,
               lambda b: zlib.decompress(b, 47)):
        try:
            return ET.fromstring(fn(blob))
        except (zlib.error, ET.ParseError):
            continue
    raise client.BiliError("弹幕 XML 解不开（未知压缩格式）", kind="api")


def _dm_item_xml(attr: list[str], text: str) -> dict:
    """XML 兜底：p='进度秒,模式,字号,颜色,时间戳,弹幕池,用户hash,弹幕id,...'。"""
    def g(i, cast=int):
        try:
            return cast(attr[i])
        except (IndexError, ValueError):
            return None

    sec = g(0, float) or 0.0
    ms = int(sec * 1000)
    color, color_dec = _color_hex(g(3))
    ctime = g(4)
    return {
        "text": text,
        "time_ms": ms,
        "time_sec": round(ms / 1000, 3),
        "time_hms": _ts(sec),
        "mode": g(1),
        "fontsize": g(2),
        "color": color,
        "color_dec": color_dec,
        "ctime": ctime,
        "time": _bj_time(ctime),
        "mid_hash": attr[6] if len(attr) > 6 else None,
        "id": attr[7] if len(attr) > 7 else None,
    }


def _seg_urls(ad, cid: int, aid: int | None, seg: int) -> list[str]:
    """分段弹幕候选地址：wbi 签名端点优先，再退回未签名端点。

    实测两条路都能出 protobuf；wbi 是官方当前要求的口径（多一次 nav 取 key，有 1h 缓存），
    未签名那条留作签名失效时的兜底，代价为零。
    """
    params = {"type": 1, "oid": cid, "pid": aid or 0, "segment_index": seg}
    urls = []
    try:
        q = client.wbi_query(params, client.wbi_mix_cached(ad.cookie))
        urls.append(f"{client.API}/x/v2/dm/wbi/web/seg.so?{q}")
    except client.BiliError:
        pass                       # nav 挂了就只剩未签名兜底
    urls.append(f"{client.API}/x/v2/dm/web/seg.so?type=1&oid={cid}"
                f"&pid={aid or 0}&segment_index={seg}")
    return urls


def _danmaku_xml(ad, cid: int) -> list[dict]:
    """XML 端点兜底：一次给整片弹幕（不分段）。"""
    blob = _get_bytes(f"{client.API}/x/v1/dm/list.so?oid={cid}", ad.cookie)
    root = _xml_root(blob)
    return [_dm_item_xml((d.get("p") or "").split(","), (d.text or "").strip())
            for d in root.iter("d")]


def _danmaku(ad, target: str, cid: int | None = None, segment: int = 1,
             limit: int = 200) -> dict:
    """分段弹幕：protobuf 优先（wbi → 未签名），解不出 / 取不到时退回 XML 端点。"""
    bvid = client.parse_bvid(target)
    v = _view(ad, target)                    # 一次 view 同时拿到 cid 和 aid（pid 参数要用）
    aid = _to_int(v.get("aid"))
    use_cid = _to_int(cid) or _to_int(v.get("cid"))
    if not use_cid:
        pages = v.get("pages") or []
        use_cid = _to_int(pages[0].get("cid")) if pages else None
    if not use_cid:
        raise client.BiliError(f"取不到 cid：{target}", kind="not_found")
    seg = max(1, _to_int(segment) or 1)
    limit = max(1, _to_int(limit) or 200)

    out = {"platform": PLATFORM, "id": bvid, "cid": use_cid, "aid": aid,
           "segment": seg, "source": None, "count": 0, "items": [], "warning": None}

    errors, blobs, elems = [], [], []
    for url in _seg_urls(ad, use_cid, aid, seg):
        try:
            blob = _get_bytes(url, ad.cookie)
            got = pb.parse_messages(blob) if blob else []
        except (client.BiliError, ValueError) as e:   # ValueError = protobuf 结构异常
            errors.append(f"{url.split('?')[0].rsplit('/', 1)[-1]}: {type(e).__name__}: {e}")
            continue
        blobs.append(blob)
        if got:
            elems = got
            break

    if elems:
        out["source"] = "protobuf"
        out["items"] = [_dm_item(e) for e in elems[:limit]]
        out["count"] = len(out["items"])
        if len(elems) > limit:
            out["truncated"] = True
        return out
    if blobs and not any(blobs):
        out["source"] = "protobuf"
        out["warning"] = "该分段没有弹幕（接口返回空）"
        return out

    try:
        xml_items = _danmaku_xml(ad, use_cid)
    except (client.BiliError, ET.ParseError) as e2:
        raise client.BiliError(
            f"弹幕取不到：protobuf({'；'.join(errors) or '解析为空'}）；"
            f"XML 兜底({type(e2).__name__}: {e2})",
            kind="network" if errors else "api") from e2
    out["source"] = "xml"
    out["warning"] = "protobuf 未解析出弹幕，已用 XML 端点兜底（整片弹幕，不分段）"
    out["items"] = xml_items[:limit]
    out["count"] = len(out["items"])
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


# ───────────────────────── 在线人数 ─────────────────────────


def _online(ad, target: str) -> dict:
    """当前在线观看人数。"""
    bvid = client.parse_bvid(target)
    use_cid = _cid_of(ad, target, None)
    data = client.get_json(
        f"{client.API}/x/player/online/total?bvid={bvid}&cid={use_cid}",
        ad.cookie).get("data") or {}
    return {
        "platform": PLATFORM, "id": bvid, "cid": use_cid,
        "online": _to_int(data.get("total")),
        "count": _to_int(data.get("count")),
        "raw": {"total": data.get("total"), "count": data.get("count")},
        "shown": data.get("show_switch") or {},
    }


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
        name="playurl",
        summary="播放地址：默认给 dash 流并做裁剪；清晰度受登录态影响（带 cookie 才有 1080P+）",
        handler=_playurl,
        params=(
            param("target", str, required=True, help="BV 号或视频 URL"),
            param("cid", int, default=None, help="分P 的 cid，默认第一P"),
            param("quality", int, default=None, help="清晰度 id（默认 80，取不到时按档位回落）"),
            param("limit", int, default=5, help="最多保留几档清晰度"),
            param("durl", bool, default=False,
                  help="True = 走老接口取 flv/mp4 分段直链，不取 dash"),
        ),
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
        name="danmaku",
        summary="分段弹幕：按时间段取一段，解析失败自动退回整片弹幕",
        handler=_danmaku,
        params=(
            param("target", str, required=True, help="BV 号或视频 URL"),
            param("cid", int, default=None, help="分P 的 cid，默认第一P"),
            param("segment", int, default=1, help="弹幕分段号，从 1 开始（每段约 6 分钟）"),
            param("limit", int, default=200, help="最多返回条数"),
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
    Capability(
        name="online",
        summary="当前在线观看人数",
        handler=_online,
        params=(param("target", str, required=True, help="BV 号或视频 URL"),),
    ),
]
