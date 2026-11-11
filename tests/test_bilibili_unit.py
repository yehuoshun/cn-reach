"""B站适配器单元测试 —— 全部离线（mock 掉网络层），零第三方依赖。

运行：
    python -m unittest discover -s tests -t .        # 标准库
    pytest -q                                        # 若装了 pytest（可直接跑本文件）
"""
import json
import unittest
import urllib.error
from unittest import mock

from adapters.bilibili import adapter as A
from adapters.bilibili import client as C
from adapters.bilibili.adapter import BilibiliAdapter, _clean, _ts

# ───────────────────────── 测试数据 ─────────────────────────

VIEW = {
    "bvid": "BV1xx411c7mD", "aid": 123, "cid": 456,
    "title": "测试视频", "desc": "简介", "owner": {"name": "UP主"},
    "duration": 300, "pubdate": 1700000000, "pic": "http://i0.hdslb.com/x.jpg",
    "pages": [{"cid": 456, "part": "P1", "duration": 100},
              {"cid": 789, "part": "P2", "duration": 200}],
    "stat": {"view": 11, "danmaku": 22, "reply": 33, "favorite": 44,
             "coin": 55, "share": 66, "like": 77},
}


class _Resp:
    """伪造成 urlopen 的上下文管理器。"""

    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ───────────────────────── 工具函数 ─────────────────────────

class UtilsTest(unittest.TestCase):
    def test_clean_strips_html(self):
        self.assertEqual(_clean('<em class="keyword">爱莉</em>希雅'), "爱莉希雅")
        self.assertEqual(_clean(None), "")

    def test_ts(self):
        self.assertEqual(_ts(0), "00:00")
        self.assertEqual(_ts(75), "01:15")
        self.assertEqual(_ts(3601), "60:01")


# ───────────────────────── BV / wbi ─────────────────────────

class BvidTest(unittest.TestCase):
    def test_parse_from_url_and_bare(self):
        self.assertEqual(C.parse_bvid("https://www.bilibili.com/video/BV1tXx56WECd/?p=1"),
                         "BV1tXx56WECd")
        self.assertEqual(C.parse_bvid("BV1tXx56WECd"), "BV1tXx56WECd")

    def test_parse_invalid(self):
        with self.assertRaises(C.BiliError) as cm:
            C.parse_bvid("这不是BV号")
        self.assertEqual(cm.exception.kind, "not_found")


class WbiTest(unittest.TestCase):
    # B站官方文档里的示例 img_key/sub_key
    IMG = "7cd084941338484aae1ad9425b84077c"
    SUB = "4932caff0ff746eab6f01bf08b70ac45"

    def test_mixin_key_known_vector(self):
        self.assertEqual(C.mixin_key(self.IMG, self.SUB),
                         "ea1db124af3c7062474693fa704f4ff8")

    def test_mixin_key_empty_raises(self):
        with self.assertRaises(C.BiliError):
            C.mixin_key("", self.SUB)

    def test_wbi_query_has_signature(self):
        q = C.wbi_query({"keyword": "x", "page": 1}, "MIXKEY")
        self.assertIn("wts=", q)
        self.assertIn("w_rid=", q)
        # w_rid == md5(除 w_rid 外的 query + mix)
        base, rid = q.rsplit("&w_rid=", 1)
        import hashlib
        self.assertEqual(rid, hashlib.md5((base + "MIXKEY").encode()).hexdigest())


# ───────────────────────── 请求层（错误归一化） ─────────────────────────

class ClientTest(unittest.TestCase):
    def test_ok_passthrough(self):
        with mock.patch.object(C.urllib.request, "urlopen", return_value=_Resp({"code": 0, "data": {"x": 1}})):
            self.assertEqual(C.get_json("http://x", "", tries=1), {"code": 0, "data": {"x": 1}})

    def test_bare_json_without_code(self):
        # 字幕 CDN 直接返回裸 JSON，没有 code 字段
        with mock.patch.object(C.urllib.request, "urlopen", return_value=_Resp({"body": [1, 2]})):
            self.assertEqual(C.get_json("http://x", "", tries=1), {"body": [1, 2]})

    def test_risk_412_immediate(self):
        with mock.patch.object(C.urllib.request, "urlopen", return_value=_Resp({"code": -412, "message": "风控"})):
            with self.assertRaises(C.BiliError) as cm:
                C.get_json("http://x", "", tries=3)
        self.assertEqual(cm.exception.kind, "risk")

    def test_business_error_no_retry(self):
        calls = {"n": 0}

        def fake(*a, **k):
            calls["n"] += 1
            return _Resp({"code": -999, "message": "boom"})

        with mock.patch.object(C.urllib.request, "urlopen", side_effect=fake):
            with self.assertRaises(C.BiliError):
                C.get_json("http://x", "", tries=3)
        self.assertEqual(calls["n"], 1)  # 业务错误不重试


# ───────────────────────── 能力层（mock 网络） ─────────────────────────

class DoctorTest(unittest.TestCase):
    def test_doctor_ok(self):
        with mock.patch.object(A.client, "nav_info",
                               return_value={"isLogin": True, "uname": "u",
                                             "level_info": {"current_level": 2}}), \
             mock.patch.object(A.client, "wbi_mix", return_value="MIX"):
            out = BilibiliAdapter(cookie="x").doctor()
        self.assertTrue(out["ok"])
        self.assertTrue(out["logged_in"])
        self.assertEqual(out["user"], "u")
        self.assertTrue(out["wbi"])

    def test_doctor_reports_error(self):
        with mock.patch.object(A.client, "nav_info",
                               side_effect=C.BiliError("boom", kind="network")):
            out = BilibiliAdapter(cookie="x").doctor()
        self.assertFalse(out["ok"])
        self.assertEqual(out["kind"], "network")


class SearchTest(unittest.TestCase):
    RAW = {"data": {"result": [
        {"bvid": "BV1aaa", "title": '<em class="keyword">爱莉</em>希雅', "author": "up1",
         "play": 100, "video_review": 5, "duration": "0:26", "pubdate": 1700000000,
         "description": "desc"},
        {"bvid": "BV1bbb", "title": "第二条", "author": "up2", "play": 200,
         "video_review": 1, "duration": "1:00", "pubdate": 0, "description": ""},
        {"bvid": None, "title": "无 bvid 应被丢弃"},
    ]}}

    def test_search_normalizes_and_limits(self):
        with mock.patch.object(A, "wbi_mix", return_value="m"), \
             mock.patch.object(A, "wbi_query", return_value="q=1"), \
             mock.patch.object(A, "get_json", return_value=self.RAW):
            out = BilibiliAdapter(cookie="").search("爱莉希雅", limit=1)
        self.assertEqual(out["count"], 1)
        it = out["items"][0]
        self.assertEqual(it["id"], "BV1aaa")
        self.assertEqual(it["title"], "爱莉希雅")           # HTML 标签被清掉
        self.assertEqual(it["url"], "https://www.bilibili.com/video/BV1aaa")
        self.assertEqual(it["pubdate"], "2023-11-15")

    def test_search_no_result_raises(self):
        with mock.patch.object(A, "wbi_mix", return_value="m"), \
             mock.patch.object(A, "wbi_query", return_value="q=1"), \
             mock.patch.object(A, "get_json", return_value={"data": {"result": None}}):
            with self.assertRaises(C.BiliError):
                BilibiliAdapter(cookie="").search("x")


class VideoTest(unittest.TestCase):
    def test_video_normalizes(self):
        with mock.patch.object(A, "get_json", return_value={"data": VIEW}):
            out = BilibiliAdapter(cookie="").video("BV1xx411c7mD")
        self.assertEqual(out["id"], "BV1xx411c7mD")
        self.assertEqual(out["duration_hms"], "05:00")
        self.assertEqual(len(out["pages"]), 2)
        self.assertEqual(out["stats"]["view"], 11)
        self.assertEqual(out["author"], "UP主")

    def test_video_not_found(self):
        with mock.patch.object(A, "get_json", return_value={"data": None}):
            with self.assertRaises(C.BiliError) as cm:
                BilibiliAdapter(cookie="").video("BV1xx411c7mD")
        self.assertEqual(cm.exception.kind, "not_found")


class SubtitleTest(unittest.TestCase):
    def _adapter_with_view(self):
        ad = BilibiliAdapter(cookie="x")
        ad._view = lambda t: VIEW
        return ad

    def test_available_and_rate(self):
        # 400 字 / 100 秒（默认第一P 时长）= 4.0 字/秒 → 正常
        items = [{"from": 0.0, "to": 1.0, "text": "字" * 400}]
        ad = self._adapter_with_view()
        ad._subtitle_once = lambda bvid, cid: (items, "ai-zh")
        out = ad.subtitles("BV1xx411c7mD", max_chars=10)
        self.assertTrue(out["available"])
        self.assertEqual(out["lang"], "ai-zh")
        self.assertEqual(out["page_duration"], 100)     # 用第一P 时长，不是总 300
        self.assertEqual(out["rate"], 4.0)
        self.assertFalse(out["suspect"])
        self.assertTrue(out["truncated"])
        self.assertEqual(out["items"][0]["ts"], "00:00")

    def test_uses_selected_page_duration(self):
        items = [{"from": 0.0, "to": 1.0, "text": "字" * 400}]
        ad = self._adapter_with_view()
        ad._subtitle_once = lambda bvid, cid: (items, "ai-zh")
        out = ad.subtitles("BV1xx411c7mD", cid=789)     # 第二P 时长 200
        self.assertEqual(out["page_duration"], 200)
        self.assertEqual(out["rate"], 2.0)

    def test_suspect_when_rate_bad_and_inconsistent(self):
        # 10 字 / 100 秒 = 0.1 字/秒 → 异常；两次结果不同 → 判不可信
        a = [{"from": 0.0, "to": 1.0, "text": "短"}]
        b = [{"from": 0.0, "to": 1.0, "text": "完全不一样的内容"}]
        ad = self._adapter_with_view()
        ad._subtitle_once = mock.Mock(side_effect=[(a, "ai-zh"), (b, "ai-zh")])
        out = ad.subtitles("BV1xx411c7mD")
        self.assertTrue(out["suspect"])
        self.assertIn("不一致", out["suspect_reason"])

    def test_fallback_to_desc_and_comments(self):
        ad = self._adapter_with_view()
        ad._subtitle_once = lambda bvid, cid: (None, None)
        ad.comments = lambda t, limit=10, sort="hot": {"items": [{"user": "u", "content": "c"}]}
        out = ad.subtitles("BV1xx411c7mD")
        self.assertFalse(out["available"])
        self.assertEqual(out["fallback"]["type"], "desc+comments")
        self.assertEqual(out["fallback"]["desc"], "简介")
        self.assertEqual(len(out["fallback"]["comments"]), 1)


class CommentsTest(unittest.TestCase):
    def test_comments_normalizes(self):
        raw = {"data": {"replies": [
            {"member": {"uname": "甲"}, "like": 9, "content": {"message": "好看"}, "ctime": 1700000000},
        ]}}
        ad = BilibiliAdapter(cookie="x")
        ad._view = lambda t: VIEW
        with mock.patch.object(A, "get_json", return_value=raw):
            out = ad.comments("BV1xx411c7mD", limit=5)
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["items"][0]["user"], "甲")
        self.assertEqual(out["items"][0]["like"], 9)


class RegistryTest(unittest.TestCase):
    def test_get_adapter(self):
        from adapters import get_adapter, list_adapters
        self.assertIn("bilibili", list_adapters())
        self.assertEqual(get_adapter("bilibili").name, "bilibili")

    def test_unknown_platform(self):
        from adapters import get_adapter
        with self.assertRaises(KeyError):
            get_adapter("nope")


if __name__ == "__main__":
    unittest.main()
