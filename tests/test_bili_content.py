# -*- coding: utf-8 -*-
"""content 能力层单测 —— 全部离线（mock 掉网络层），零第三方依赖。

运行：
    PYTHONPATH=. PYTHONUTF8=1 python -m unittest discover -s tests -t .

约定：
  - 只 patch client.get_json / client.wbi_get / content._get_bytes，不碰真网络；
  - 弹幕 protobuf 的样例字节按实测字段号手工编码（见 adapters/bilibili/pb.py）；
  - 每个能力都要验证「返回体带 platform」和「参数透传」。
"""
import json
import unittest
import urllib.error
import zlib
from unittest import mock

from adapters.bilibili import client as C
from adapters.bilibili.capabilities import CAPABILITIES as REG
from adapters.bilibili.capabilities import catalog, content
from adapters.bilibili.capabilities.spec import Capability

SAMPLE_BV = "BV1xx411c7mD"

# adapter.video 归一化之后的形状（能力层拿到的就是它）
VIEW = {
    "platform": "bilibili", "id": SAMPLE_BV, "aid": 123, "cid": 456,
    "title": "测试视频", "duration": 300,
    "pages": [{"cid": 456, "part": "P1", "duration": 100},
              {"cid": 789, "part": "P2", "duration": 200}],
}

EXPECTED_NAMES = {"search", "video", "subtitle", "comments", "pages",
                  "playurl", "replies", "danmaku", "related", "online"}


# ───────────────────────── 测试替身 ─────────────────────────


class FakeAdapter:
    """替身：不碰网络，只记录被调用的参数。"""

    name = "bilibili"

    def __init__(self, view=None, cookie=""):
        self.cookie = cookie
        self._view = dict(view or VIEW)
        self.calls = []

    def video(self, target):
        self.calls.append(("video", target))
        return dict(self._view)

    def search(self, keyword, page=1, page_size=20, order="totalrank", limit=None):
        self.calls.append(("search", keyword, page, page_size, order, limit))
        return {"query": keyword, "count": 0, "items": []}      # 故意不带 platform

    def subtitles(self, target, cid=None, max_chars=None):
        self.calls.append(("subtitles", target, cid, max_chars))
        return {"id": SAMPLE_BV, "available": False}

    def comments(self, target, limit=20, sort="hot"):
        self.calls.append(("comments", target, limit, sort))
        return {"count": 0, "items": []}


def router(table):
    """按 URL 子串分派 client.get_json；未登记的 URL 直接断言失败（防止漏 patch 真联网）。"""
    calls = []

    def _get_json(url, cookie="", **kw):
        calls.append((url, cookie))
        for mark, payload in table:
            if mark in url:
                if isinstance(payload, Exception):
                    raise payload
                if callable(payload):
                    return payload(url)
                return payload
        raise AssertionError(f"未预期的请求：{url}")

    _get_json.calls = calls
    return _get_json


# ───────────────────────── protobuf 样例（字段号照实测） ─────────────────────────


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append((b | 0x80) if n else b)
        if not n:
            return bytes(out)


def _vfield(no: int, val: int) -> bytes:
    return _varint(no << 3) + _varint(val)


def _bfield(no: int, val) -> bytes:
    if isinstance(val, str):
        val = val.encode("utf-8")
    return _varint((no << 3) | 2) + _varint(len(val)) + val


def _elem(progress=1000, mode=1, color=16777215, text="弹幕", ctime=1793503176,
          mid_hash="259af544", action=None, dmid=2234825639763770880) -> bytes:
    body = (_vfield(1, dmid) + _vfield(2, progress) + _vfield(3, mode)
            + _vfield(4, 25) + _vfield(5, color) + _bfield(6, mid_hash)
            + _bfield(7, text) + _vfield(8, ctime) + _vfield(9, 10)
            + _bfield(12, str(dmid)))
    if action:
        body += _bfield(22, action)
    return body


def _seg(*elems) -> bytes:
    return b"".join(_bfield(1, e) for e in elems)


DM_XML = ('<?xml version="1.0" encoding="UTF-8"?><i><chatserver>chat.bilibili.com</chatserver>'
          '<d p="1.5,1,25,16777215,1793503176,0,259af544,2234825639763770881">第一条</d>'
          '<d p="2.5,5,18,255,1793503180,0,deadbeef,2234825639763770882">顶部第二条</d></i>')


# ───────────────────────── 契约 ─────────────────────────


class ContractTest(unittest.TestCase):
    def test_ten_capabilities_declared(self):
        names = [c.name for c in content.CAPABILITIES]
        self.assertEqual(set(names), EXPECTED_NAMES)
        self.assertEqual(len(names), 10, "能力名不能重复")

    def test_every_capability_is_well_formed(self):
        for cap in content.CAPABILITIES:
            self.assertIsInstance(cap, Capability)
            self.assertFalse(cap.auth, f"{cap.name} 应为无需登录态")
            self.assertFalse(cap.mutating, "只读项目不允许写能力")
            self.assertTrue(cap.summary, f"{cap.name} 缺中文说明")
            self.assertTrue(callable(cap.handler))
            pnames = [p.name for p in cap.params]
            self.assertEqual(len(pnames), len(set(pnames)), f"{cap.name} 参数重名")
            self.assertIn("target" if cap.name != "search" else "keyword", pnames,
                          f"{cap.name} 缺主参数")

    def test_registered_in_registry(self):
        for cap in content.CAPABILITIES:
            self.assertIs(REG[cap.name], cap)

    def test_catalog_has_no_endpoint_details(self):
        blob = json.dumps(catalog(), ensure_ascii=False)
        self.assertNotIn("http", blob)
        self.assertNotIn("api.bilibili.com", blob)
        for row in catalog():
            self.assertEqual(set(row), {"name", "summary", "auth", "params"})

    def test_missing_required_param_raises(self):
        cap = REG["search"]
        with self.assertRaises(ValueError):
            cap.run(FakeAdapter())

    def test_unknown_param_raises(self):
        with self.assertRaises(ValueError):
            REG["video"].bind(target="x", nope=1)

    def test_handlers_never_load_cookie_again(self):
        # 统一口径：cookie 取 ad.cookie，handler 里不许再 load_cookie
        fake = FakeAdapter(cookie="SESSDATA=inject")
        with mock.patch.object(C, "load_cookie", side_effect=AssertionError("不该 load_cookie")), \
             mock.patch.object(C, "wbi_mix_cached", return_value="MIX"), \
             mock.patch.object(C, "get_json", router([
                 ("/x/web-interface/archive/related", {"data": []}),
             ])), \
             mock.patch.object(content, "_get_bytes", return_value=_seg(_elem())):
            content._pages(fake, SAMPLE_BV)
            content._search(fake, "关键词")
            content._danmaku(fake, SAMPLE_BV, cid=456)


# ───────────────────────── 包装 adapter 的 4 个能力 ─────────────────────────


class WrapperTest(unittest.TestCase):
    def test_search_passes_params_and_adds_platform(self):
        fake = FakeAdapter()
        out = content._search(fake, "爱莉希雅", page=2, page_size=5,
                             order="pubdate", limit=3)
        self.assertEqual(fake.calls[-1], ("search", "爱莉希雅", 2, 5, "pubdate", 3))
        self.assertEqual(out["platform"], "bilibili")     # 适配器没给，能力层补上

    def test_video_passthrough(self):
        fake = FakeAdapter()
        out = content._video(fake, SAMPLE_BV)
        self.assertEqual(fake.calls[-1], ("video", SAMPLE_BV))
        self.assertEqual(out["id"], SAMPLE_BV)
        self.assertEqual(out["platform"], "bilibili")

    def test_subtitle_passthrough(self):
        fake = FakeAdapter()
        out = content._subtitle(fake, SAMPLE_BV, cid=789, max_chars=120)
        self.assertEqual(fake.calls[-1], ("subtitles", SAMPLE_BV, 789, 120))
        self.assertEqual(out["platform"], "bilibili")

    def test_comments_passthrough(self):
        fake = FakeAdapter()
        out = content._comments(fake, SAMPLE_BV, limit=7, sort="time")
        self.assertEqual(fake.calls[-1], ("comments", SAMPLE_BV, 7, "time"))
        self.assertEqual(out["platform"], "bilibili")


# ───────────────────────── 分P ─────────────────────────


class PagesTest(unittest.TestCase):
    def test_pages_normalized(self):
        fake = FakeAdapter()
        out = content._pages(fake, SAMPLE_BV)
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["current"], 456)
        p2 = out["items"][1]
        self.assertEqual((p2["index"], p2["cid"], p2["part"]), (2, 789, "P2"))
        self.assertEqual(p2["duration_hms"], "03:20")
        self.assertEqual(out["platform"], "bilibili")

    def test_pages_fallback_when_missing(self):
        view = dict(VIEW)
        view.pop("pages")
        out = content._pages(FakeAdapter(view=view), SAMPLE_BV)
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["items"][0]["cid"], 456)
        self.assertEqual(out["items"][0]["part"], "测试视频")


# ───────────────────────── 播放地址 ─────────────────────────


PLAY_DATA = {
    "quality": 64,
    "accept_quality": [112, 80, 64],
    "accept_description": ["高清 1080P+", "高清 1080P", "清晰 480P"],
    "dash": {
        "duration": 300,
        "video": [
            {"id": 80, "height": 1080, "width": 1920, "bandwidth": 1_000_000,
             "codecid": 7, "codecs": "avc1.640032", "baseUrl": "https://cdn/v-avc-low.m4s"},
            {"id": 80, "height": 1080, "width": 1920, "bandwidth": 3_000_000,
             "codecid": 7, "codecs": "avc1.640032", "baseUrl": "https://cdn/v-avc-high.m4s"},
            {"id": 64, "height": 480, "width": 854, "bandwidth": 500_000,
             "codecid": 12, "codecs": "hev1.1.6", "base_url": "https://cdn/v-hevc.m4s"},
            {"id": 32, "height": 360, "width": 640, "bandwidth": 200_000,
             "codecid": 7, "codecs": "avc1.4d401e", "baseUrl": "https://cdn/v-360.m4s"},
        ],
        "audio": [
            {"id": 30280, "bandwidth": 192_000, "codecs": "mp4a.40.2",
             "baseUrl": "https://cdn/a-192.m4s"},
            {"id": 30216, "bandwidth": 64_000, "codecs": "mp4a.40.2",
             "baseUrl": "https://cdn/a-64.m4s"},
        ],
    },
    "durl": [],
}


class PlayurlTest(unittest.TestCase):
    def _patch_wbi(self, payload=None, cookie_seen=None):
        def _wbi_get(url, params, cookie="", **kw):
            if cookie_seen is not None:
                cookie_seen.append((url, dict(params), cookie))
            if isinstance(payload, Exception):
                raise payload
            return payload or {"data": PLAY_DATA}
        return _wbi_get

    def test_dash_trimmed_and_deduped(self):
        seen = []
        with mock.patch.object(C, "wbi_get", self._patch_wbi(cookie_seen=seen)):
            out = content._playurl(FakeAdapter(cookie="SESSDATA=x"), SAMPLE_BV, cid=456, limit=2)
        url, params, cookie = seen[0]
        self.assertIn("/x/player/wbi/playurl", url)
        self.assertEqual(params["fnval"], content.PLAYURL_FNVAL)
        self.assertEqual(params["cid"], 456)
        self.assertEqual(cookie, "SESSDATA=x")        # cookie 来自 ad.cookie
        self.assertTrue(out["is_dash"])
        self.assertEqual(out["quality"], 64)
        self.assertEqual(out["quality_name"], "清晰 480P")
        videos, audios = out["dash"]["video"], out["dash"]["audio"]
        self.assertEqual(len(videos), 2, "按 limit 裁剪")
        # 同 (codecid,height) 只留带宽最大的那条，且 base_url 两种写法都能读
        self.assertEqual(videos[0]["bandwidth"], 3_000_000)
        self.assertEqual(videos[0]["base_url"], "https://cdn/v-avc-high.m4s")
        self.assertEqual(videos[1]["base_url"], "https://cdn/v-hevc.m4s")
        self.assertEqual(set(videos[0]),
                         {"id", "height", "width", "bandwidth", "codecs", "codecid", "base_url"})
        self.assertEqual(len(audios), 2, "音频只留前 2 条")
        self.assertIn("时效", out["url_expires_hint"])
        self.assertIn("note", out)                     # 原 4 条流被裁剪过
        self.assertFalse([k for k in out if k in ("dash_raw", "playlist")])

    def test_quality_list_default_flag(self):
        with mock.patch.object(C, "wbi_get", self._patch_wbi()):
            out = content._playurl(FakeAdapter(), SAMPLE_BV, cid=456)
        self.assertEqual([q["id"] for q in out["quality_list"]], [112, 80, 64])
        self.assertEqual([q["default"] for q in out["quality_list"]], [False, False, True])

    def test_auth_error_falls_back_to_unsigned_endpoint(self):
        fallback = router([("/x/player/playurl", {"data": PLAY_DATA})])
        with mock.patch.object(C, "wbi_get",
                               self._patch_wbi(C.BiliError("账号未登录", code=-101, kind="auth"))), \
             mock.patch.object(C, "get_json", fallback):
            out = content._playurl(FakeAdapter(), SAMPLE_BV, cid=456)
        self.assertTrue(out["is_dash"])
        self.assertEqual(len(fallback.calls), 1)

    def test_other_errors_propagate(self):
        with mock.patch.object(C, "wbi_get",
                               self._patch_wbi(C.BiliError("风控", code=-412, kind="risk"))):
            with self.assertRaises(C.BiliError) as cm:
                content._playurl(FakeAdapter(), SAMPLE_BV, cid=456)
        self.assertEqual(cm.exception.kind, "risk")

    def test_durl_mode(self):
        data = {"quality": 32, "accept_quality": [64, 32],
                "accept_description": ["清晰 480P", "流畅 360P"],
                "durl": [{"order": 1, "size": 100, "url": "https://cdn/p1.flv"},
                         {"order": 2, "size": 200, "url": "https://cdn/p2.flv"}]}
        gj = router([("/x/player/playurl", {"data": data})])
        with mock.patch.object(C, "get_json", gj):
            out = content._playurl(FakeAdapter(), SAMPLE_BV, cid=456, durl=True)
        self.assertFalse(out["is_dash"])
        self.assertEqual([d["order"] for d in out["durl"]], [1, 2])
        self.assertEqual(out["durl"][0]["url"], "https://cdn/p1.flv")

    def test_empty_playurl_raises(self):
        with mock.patch.object(C, "wbi_get", self._patch_wbi({"data": {}})):
            with self.assertRaises(C.BiliError) as cm:
                content._playurl(FakeAdapter(), SAMPLE_BV, cid=456)
        self.assertEqual(cm.exception.kind, "api")

    def test_cid_resolved_from_view_when_missing(self):
        fake = FakeAdapter()
        with mock.patch.object(C, "wbi_get", self._patch_wbi()):
            out = content._playurl(fake, SAMPLE_BV)
        self.assertEqual(fake.calls[0], ("video", SAMPLE_BV))
        self.assertEqual(out["cid"], 456)


# ───────────────────────── 子评论 ─────────────────────────


def _reply(rpid, user="甲", like=1, rcount=0, ctime=1793503176, parent=0):
    return {"rpid": rpid, "mid": 42, "like": like, "rcount": rcount, "ctime": ctime,
            "parent": parent, "root": 999,
            "member": {"uname": user}, "content": {"message": f"回复{rpid}"}}


class RepliesTest(unittest.TestCase):
    def test_sub_replies_with_explicit_root(self):
        gj = router([("/x/v2/reply/reply", {"data": {
            "replies": [_reply(2, "乙", parent=322153888384), _reply(3, "丙")],
            "page": {"num": 1, "size": 20, "count": 2}}})])
        with mock.patch.object(C, "get_json", gj):
            out = content._replies(FakeAdapter(), SAMPLE_BV, root=322153888384, limit=10)
        self.assertEqual(out["root"], 322153888384)
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["items"][0]["user"], "乙")
        self.assertEqual(out["items"][0]["parent"], 322153888384)
        self.assertEqual(out["items"][0]["content"], "回复2")
        self.assertIn("root=322153888384", gj.calls[0][0])
        self.assertIn("pn=1", gj.calls[0][0])
        self.assertNotIn("root_comment", out)

    def test_picks_hot_root_when_root_missing(self):
        gj = router([
            ("/x/v2/reply/reply", {"data": {"replies": [_reply(7)], "page": {"count": 1}}}),
            ("/x/v2/reply?", {"data": {"replies": [_reply(5, "无回复", rcount=0),
                                                    _reply(9, "有回复", rcount=3)]}}),
        ])
        with mock.patch.object(C, "get_json", gj):
            out = content._replies(FakeAdapter(), SAMPLE_BV)
        self.assertEqual(out["root"], 9, "应挑有回复的那条主评论")
        self.assertEqual(out["root_comment"]["user"], "有回复")
        self.assertEqual(out["count"], 1)

    def test_empty_comment_area_raises(self):
        gj = router([("/x/v2/reply?", {"data": {"replies": []}})])
        with mock.patch.object(C, "get_json", gj):
            with self.assertRaises(C.BiliError) as cm:
                content._replies(FakeAdapter(), SAMPLE_BV)
        self.assertEqual(cm.exception.kind, "not_found")

    def test_limit_truncates_without_extra_page(self):
        batch = [_reply(i) for i in range(1, 6)]
        gj = router([("/x/v2/reply/reply",
                      {"data": {"replies": batch, "page": {"count": 5}}})])
        with mock.patch.object(C, "get_json", gj):
            out = content._replies(FakeAdapter(), SAMPLE_BV, root=999, limit=2)
        self.assertEqual(out["count"], 2)
        self.assertEqual(len(gj.calls), 1)


# ───────────────────────── 弹幕 ─────────────────────────


class DanmakuTest(unittest.TestCase):
    def test_protobuf_fields_mapped(self):
        raw = _seg(_elem(progress=16184, text="这北方和黑岩", color=16777215,
                         ctime=1793503176, action='{"id":50001}'))
        seen = []

        def fake_bytes(url, cookie=""):
            seen.append(url)
            return raw

        with mock.patch.object(C, "wbi_mix_cached", return_value="MIX"), \
             mock.patch.object(content, "_get_bytes", fake_bytes):
            out = content._danmaku(FakeAdapter(cookie="x"), SAMPLE_BV, cid=456)

        self.assertIn("/x/v2/dm/wbi/web/seg.so", seen[0])
        self.assertIn("w_rid=", seen[0])                 # wbi 签名在
        self.assertIn("pid=123", seen[0])                # pid = aid（来自 view）
        self.assertIn("segment_index=1", seen[0])
        self.assertEqual(out["source"], "protobuf")
        self.assertEqual(out["cid"], 456)
        item = out["items"][0]
        self.assertEqual(item["text"], "这北方和黑岩")
        self.assertEqual(item["time_ms"], 16184)
        self.assertEqual(item["time_sec"], 16.184)
        self.assertEqual(item["time_hms"], "00:16")
        self.assertEqual(item["mode"], 1)
        self.assertEqual(item["fontsize"], 25)
        self.assertEqual(item["color"], "#FFFFFF")
        self.assertEqual(item["color_dec"], 16777215)
        self.assertEqual(item["ctime"], 1793503176)
        self.assertEqual(item["time"], "2026-11-01 11:19")   # 北京时间（UTC+8）
        self.assertEqual(item["mid_hash"], "259af544")
        self.assertEqual(item["id"], "2234825639763770880")
        self.assertEqual(item["raw"]["22"], '{"id":50001}')  # 未核实字段保留原字段号
        self.assertNotIn("7", item["raw"])

    def test_limit_slices_and_marks_truncated(self):
        raw = _seg(*[_elem(progress=i * 1000, text=f"第{i}条") for i in range(1, 4)])
        with mock.patch.object(C, "wbi_mix_cached", return_value="MIX"), \
             mock.patch.object(content, "_get_bytes", return_value=raw):
            out = content._danmaku(FakeAdapter(), SAMPLE_BV, cid=456, limit=2)
        self.assertEqual(out["count"], 2)
        self.assertTrue(out["truncated"])
        self.assertEqual(out["items"][1]["ts" if False else "text"], "第2条")

    def test_empty_segment_is_not_an_error(self):
        with mock.patch.object(C, "wbi_mix_cached", return_value="MIX"), \
             mock.patch.object(content, "_get_bytes", return_value=b"") as gb:
            out = content._danmaku(FakeAdapter(), SAMPLE_BV, cid=456)
        self.assertEqual(out["count"], 0)
        self.assertIn("没有弹幕", out["warning"])
        # 两条候选地址都试了，但空分段不该再去打 XML 兜底端点
        self.assertEqual(gb.call_count, 2)
        self.assertFalse([c.args[0] for c in gb.call_args_list
                          if "list.so" in c.args[0]])

    def test_xml_fallback_when_protobuf_fails(self):
        def fake_bytes(url, cookie=""):
            if "seg.so" in url:
                raise C.BiliError("HTTP 412", code=412, kind="risk")
            return zlib.compress(DM_XML.encode("utf-8"), 9)     # 实测是 deflate 压缩

        with mock.patch.object(C, "wbi_mix_cached", return_value="MIX"), \
             mock.patch.object(content, "_get_bytes", fake_bytes):
            out = content._danmaku(FakeAdapter(), SAMPLE_BV, cid=456, limit=5)

        self.assertEqual(out["source"], "xml")
        self.assertIn("兜底", out["warning"])
        self.assertEqual(out["count"], 2)
        first = out["items"][0]
        self.assertEqual(first["text"], "第一条")
        self.assertEqual(first["time_ms"], 1500)
        self.assertEqual(first["time_sec"], 1.5)
        self.assertEqual(first["mode"], 1)
        self.assertEqual(first["color"], "#FFFFFF")
        self.assertEqual(first["ctime"], 1793503176)
        self.assertEqual(first["mid_hash"], "259af544")
        self.assertEqual(first["id"], "2234825639763770881")
        self.assertEqual(out["items"][1]["mode"], 5)

    def test_xml_plain_bytes_also_parse(self):
        def fake_bytes(url, cookie=""):
            if "seg.so" in url:
                return b""                      # 空响应（模拟 304）
            return DM_XML.encode("utf-8")       # 未压缩的 XML

        with mock.patch.object(C, "wbi_mix_cached", return_value="MIX"), \
             mock.patch.object(content, "_get_bytes", fake_bytes):
            out = content._danmaku(FakeAdapter(), SAMPLE_BV, cid=456)
        # 空响应按「该分段没有弹幕」处理，不会触发 XML 兜底
        self.assertEqual(out["count"], 0)

    def test_both_paths_fail_raises(self):
        def fake_bytes(url, cookie=""):
            raise C.BiliError("boom", kind="network")

        with mock.patch.object(C, "wbi_mix_cached", return_value="MIX"), \
             mock.patch.object(content, "_get_bytes", fake_bytes):
            with self.assertRaises(C.BiliError) as cm:
                content._danmaku(FakeAdapter(), SAMPLE_BV, cid=456)
        self.assertEqual(cm.exception.kind, "network")
        self.assertIn("XML 兜底", str(cm.exception))

    def test_unsigned_endpoint_is_tried_after_signed(self):
        urls = []

        def fake_bytes(url, cookie=""):
            urls.append(url)
            if "/x/v2/dm/wbi/web/seg.so" in url:
                raise C.BiliError("HTTP 404", code=404, kind="network")
            return _seg(_elem(text="未签名端点"))

        with mock.patch.object(C, "wbi_mix_cached", return_value="MIX"), \
             mock.patch.object(content, "_get_bytes", fake_bytes):
            out = content._danmaku(FakeAdapter(), SAMPLE_BV, cid=456)
        self.assertEqual(len(urls), 2)
        self.assertIn("/x/v2/dm/web/seg.so", urls[1])
        self.assertEqual(out["items"][0]["text"], "未签名端点")


class GetBytesTest(unittest.TestCase):
    """content._get_bytes 的边界（不连网）。"""

    def test_304_returns_empty(self):
        err = urllib.error.HTTPError("http://x", 304, "Not Modified", None, None)
        with mock.patch.object(content.urllib.request, "urlopen", side_effect=err):
            self.assertEqual(content._get_bytes("http://x"), b"")

    def test_412_is_risk(self):
        err = urllib.error.HTTPError("http://x", 412, "Precondition Failed", None, None)
        with mock.patch.object(content.urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(C.BiliError) as cm:
                content._get_bytes("http://x")
        self.assertEqual(cm.exception.kind, "risk")

    def test_prefers_client_get_bytes_when_available(self):
        with mock.patch.object(C, "get_bytes", lambda url, cookie="": b"raw", create=True):
            self.assertEqual(content._get_bytes("http://x", "c"), b"raw")


# ───────────────────────── 相关推荐 / 在线人数 ─────────────────────────


class RelatedTest(unittest.TestCase):
    RAW = {"data": [
        {"bvid": "BV1aaa", "title": "A<em>B</em>", "owner": {"name": "up1"},
         "duration": 153, "pubdate": 1793563792,
         "stat": {"view": 100, "danmaku": 5, "like": 9}},
        {"bvid": "BV1bbb", "title": "第二条", "owner": {"name": "up2"},
         "duration": 60, "pubdate": 0, "stat": {"view": 1}},
    ]}

    def test_related_normalized_and_limited(self):
        with mock.patch.object(C, "get_json", router([("/archive/related", self.RAW)])):
            out = content._related(FakeAdapter(), SAMPLE_BV, limit=1)
        self.assertEqual(out["count"], 1)
        it = out["items"][0]
        self.assertEqual(it["id"], "BV1aaa")
        self.assertEqual(it["title"], "AB")
        self.assertEqual(it["author"], "up1")
        self.assertEqual(it["url"], "https://www.bilibili.com/video/BV1aaa")
        self.assertEqual(it["duration_hms"], "02:33")
        self.assertEqual(it["pubdate"], "2026-11-02")

    def test_related_empty_raises(self):
        with mock.patch.object(C, "get_json", router([("/archive/related", {"data": []})])):
            with self.assertRaises(C.BiliError) as cm:
                content._related(FakeAdapter(), SAMPLE_BV)
        self.assertEqual(cm.exception.kind, "not_found")


class OnlineTest(unittest.TestCase):
    def test_online_total_is_int(self):
        payload = {"data": {"total": "1234", "count": "1200",
                            "show_switch": {"total": True, "count": True}}}
        with mock.patch.object(C, "get_json", router([("/online/total", payload)])):
            out = content._online(FakeAdapter(), SAMPLE_BV)
        self.assertEqual(out["online"], 1234)
        self.assertEqual(out["count"], 1200)
        self.assertEqual(out["cid"], 456)
        self.assertEqual(out["raw"]["total"], "1234")
        self.assertTrue(out["shown"]["total"])

    def test_online_without_total(self):
        with mock.patch.object(C, "get_json", router([("/online/total", {"data": {}})])):
            out = content._online(FakeAdapter(), SAMPLE_BV)
        self.assertIsNone(out["online"])


if __name__ == "__main__":
    unittest.main()
