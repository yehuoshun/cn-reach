# -*- coding: utf-8 -*-
"""site.py（L1 站点结构能力）离线单测 —— 全部 mock 掉请求层，零第三方依赖、不联网。

运行：
    PYTHONPATH=. PYTHONUTF8=1 python -m unittest discover -s tests -t .
"""
import unittest
from unittest import mock

from adapters.bilibili import client as C
from adapters.bilibili.capabilities import site as S

# ───────────────────────── 夹具 ─────────────────────────

V1 = {
    "aid": 1, "bvid": "BV1aaa1111aa", "title": '<em class="keyword">标题</em>甲',
    "owner": {"mid": 9, "name": "UP甲"},
    "stat": {"view": 100, "danmaku": 5, "reply": 3, "favorite": 2,
             "coin": 1, "share": 0, "like": 7},
    "pubdate": 1700000000, "duration": 256, "tname": "动画", "pic": "http://x/1.jpg",
}
V2 = {
    "aid": 2, "bvid": "BV1bbb2222bb", "title": "第二条", "owner": {"mid": 10, "name": "UP乙"},
    "stat": {"view": 200}, "pubdate": 0, "duration": 3725, "tname": "游戏", "pic": "http://x/2.jpg",
}

SEASON = {"code": 0, "result": {
    "season_id": 47836, "season_title": "鬼灭之刃 柱训练篇", "title": "鬼灭之刃 柱训练篇",
    "total": 8, "type": 1, "new_ep": {"desc": "已完结, 全8话"},
    "publish": {"is_finish": 1, "pub_time": "2024-06-22 20:00:00"},
    "rating": {"score": 9.7, "count": 7591},
    "stat": {"views": 265200354, "danmakus": 1925946, "favorites": 17701586,
             "coins": 596569, "likes": 2243132, "reply": 50127, "share": 52966,
             "follow_text": "2020.7万系列追番"},
    "areas": [{"name": "日本"}], "styles": [{"name": "热血"}],
    "up_info": {"mid": 1, "uname": "哔哩哔哩番剧", "avatar": "a.png"},
    "link": "https://www.bilibili.com/bangumi/play/ss47836",
    "episodes": [{"id": 826497, "ep_id": 826497, "title": "1", "long_title": "为了消灭",
                  "pub_time": 1719057600, "duration": 2938060, "bvid": "BV15i421v7FQ"}],
}}

DYN = {"code": 0, "data": {
    "offset": "1163608706112290850", "has_more": True,
    "items": [
        {"id_str": "1265772221318561798", "type": "DYNAMIC_TYPE_AV",
         "modules": {
             "module_author": {"name": "碧诗", "mid": 2, "pub_ts": 1700000000},
             "module_stat": {"like": {"count": 10}, "comment": {"count": 2},
                             "forward": {"count": 1}},
             "module_dynamic": {"desc": {"text": "今天的视频"},
                                "major": {"type": "MAJOR_TYPE_ARCHIVE",
                                          "archive": {"title": "视频标题", "bvid": "BV1x",
                                                      "jump_url": "//www.bilibili.com/video/BV1x"}}},
         }},
        {"id_str": "2", "type": "DYNAMIC_TYPE_WORD",
         "modules": {
             "module_author": {"name": "碧诗", "mid": 2,
                               "publish_time": "2024-01-01 12:00:00"},
             "module_dynamic": {"desc": {"text": "纯文字动态内容"}},
         }},
    ],
}}


class FakeAdapter:
    """只提供 handler 真正会用的两个属性。"""

    name = "bilibili"

    def __init__(self, cookie=""):
        self.cookie = cookie


class Recorder:
    """记录请求并依次吐出预置响应，用来同时断言「打了哪个 URL」和「怎么解析」。

    兼容两种调用形状：get_json(url, cookie) 与 wbi_get(url, params, cookie)。
    """

    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.urls = []
        self.cookies = []
        self.params = []

    def __call__(self, url, *args, **kw):
        self.urls.append(url)
        if args and isinstance(args[0], dict):        # wbi_get：第二个位置参数是 params
            self.params.append(args[0])
            cookie = args[1] if len(args) > 1 else kw.get("cookie", "")
        else:                                         # get_json：第二个位置参数是 cookie
            cookie = args[0] if args else kw.get("cookie", "")
        self.cookies.append(cookie)
        if not self.payloads:
            raise AssertionError(f"多余请求：{url}")
        return self.payloads.pop(0)


def cap(name):
    for c in S.CAPABILITIES:
        if c.name == name:
            return c
    raise AssertionError(f"没有能力 {name}")


def run(name, ad=None, **kwargs):
    return cap(name).run(ad or FakeAdapter(), **kwargs)


# ───────────────────────── 渲染小工具 ─────────────────────────

class HelperTest(unittest.TestCase):
    def test_bj_time_is_timezone_independent(self):
        # 1700000000 = 2023-11-14 22:13:20 UTC = 2023-11-15 06:13:20 北京时间
        self.assertEqual(S._bj_time(1700000000), "2023-11-15 06:13")
        self.assertEqual(S._bj_time(0), "1970-01-01 08:00")

    def test_time_returns_none_for_empty(self):
        self.assertIsNone(S._time(0))
        self.assertIsNone(S._time(None))
        self.assertEqual(S._time(1700000000, "%Y-%m-%d"), "2023-11-15")

    def test_clip_drops_zero_placeholders(self):
        # 没开播时直播接口给的就是这个占位串
        self.assertIsNone(S._clip("0000-00-00 00:00:00"))
        self.assertIsNone(S._clip(""))
        self.assertEqual(S._clip("2024-06-22 20:00:00"), "2024-06-22 20:00")

    def test_fix_url(self):
        self.assertEqual(S._fix_url("//www.bilibili.com/video/BV1x"),
                         "https://www.bilibili.com/video/BV1x")
        self.assertEqual(S._fix_url("http://www.bilibili.com/x"),
                         "https://www.bilibili.com/x")
        self.assertIsNone(S._fix_url(""))

    def test_dur(self):
        self.assertEqual(S._dur(256), "04:16")
        self.assertEqual(S._dur(3725), "1:02:05")
        self.assertEqual(S._dur(None), "00:00")

    def test_clean_strips_tags_and_unescapes(self):
        self.assertEqual(S._clean('<em class="keyword">爱莉</em>希雅'), "爱莉希雅")
        self.assertEqual(S._clean("A &amp; B"), "A & B")
        self.assertEqual(S._clean(None), "")


# ───────────────────────── 注册表 ─────────────────────────

class RegistryTest(unittest.TestCase):
    NAMES = ["ranking", "popular", "up_info", "up_videos", "season", "dynamic"]

    def test_six_capabilities(self):
        self.assertEqual([c.name for c in S.CAPABILITIES], self.NAMES)
        for c in S.CAPABILITIES:
            self.assertTrue(c.summary.strip(), f"{c.name} 缺中文说明")
            self.assertFalse(c.auth, f"{c.name} 应不需要登录态")
            self.assertFalse(c.mutating)

    def test_registered_in_global_table(self):
        from adapters.bilibili.capabilities import CAPABILITIES
        for name in self.NAMES:
            self.assertIn(name, CAPABILITIES)

    def test_required_params(self):
        self.assertTrue(next(p for p in cap("up_info").params if p.name == "mid").required)
        self.assertTrue(next(p for p in cap("up_videos").params if p.name == "mid").required)
        season = {p.name: p for p in cap("season").params}
        self.assertFalse(season["season_id"].required)
        self.assertFalse(season["ep_id"].required)
        self.assertEqual({p.name for p in cap("dynamic").params},
                         {"mid", "offset", "limit"})

    def test_unknown_param_rejected(self):
        with self.assertRaises(ValueError):
            cap("ranking").bind(nope=1)


# ───────────────────────── 排行 / 热门 / 分区 ─────────────────────────

class RankingTest(unittest.TestCase):
    def test_normalizes_and_limits(self):
        rec = Recorder({"code": 0, "data": {"note": "综合质量", "list": [V1, V2]}})
        with mock.patch.object(C, "get_json", rec):
            out = run("ranking", FakeAdapter("CK"), limit=1)
        self.assertIn("/x/web-interface/ranking/v2?rid=0&type=all", rec.urls[0])
        self.assertEqual(rec.cookies[0], "CK")          # cookie 由 adapter 注入
        self.assertEqual(out["platform"], "bilibili")
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["note"], "综合质量")
        it = out["items"][0]
        self.assertEqual(it["title"], "标题甲")
        self.assertEqual(it["author"], "UP甲")
        self.assertEqual(it["url"], "https://www.bilibili.com/video/BV1aaa1111aa")
        self.assertEqual(it["pubdate"], "2023-11-15 06:13")
        self.assertEqual(it["duration_hms"], "04:16")
        self.assertEqual(it["stats"]["view"], 100)

    def test_empty_raises(self):
        with mock.patch.object(C, "get_json", Recorder({"code": 0, "data": {"list": []}})):
            with self.assertRaises(C.BiliError) as cm:
                run("ranking")
        self.assertEqual(cm.exception.kind, "not_found")


class PopularTest(unittest.TestCase):
    def test_url_and_no_more(self):
        rec = Recorder({"code": 0, "data": {"list": [V1], "no_more": 1}})
        with mock.patch.object(C, "get_json", rec):
            out = run("popular", page=2, page_size=5)
        self.assertIn("/x/web-interface/popular?pn=2&ps=5", rec.urls[0])
        self.assertTrue(out["no_more"])
        self.assertEqual(out["count"], 1)

    def test_empty_raises(self):
        with mock.patch.object(C, "get_json", Recorder({"code": 0, "data": {}})):
            with self.assertRaises(C.BiliError) as cm:
                run("popular")
        self.assertEqual(cm.exception.kind, "not_found")


# ───────────────────────── UP主 ─────────────────────────

class UpInfoTest(unittest.TestCase):
    PAYLOAD = {"code": 0, "data": {"mid": 2, "name": "碧诗", "sex": "男", "sign": "签名",
                                   "level": 6, "rank": 20000, "face": "f.jpg", "jointime": 0}}

    def test_fields(self):
        rec = Recorder(self.PAYLOAD)
        with mock.patch.object(C, "ensure_buvid", return_value="buvid3=1"), \
             mock.patch.object(C, "wbi_get", rec):
            out = run("up_info", mid=2)
        self.assertIn("/x/space/wbi/acc/info", rec.urls[0])
        self.assertEqual(rec.params[0]["mid"], 2)
        self.assertEqual(out["name"], "碧诗")
        self.assertEqual(out["level"], 6)
        self.assertIsNone(out["jointime"])              # 0 不渲染成 1970
        self.assertEqual(out["url"], "https://space.bilibili.com/2")
        self.assertEqual(out["platform"], "bilibili")

    def test_uses_buvid_and_retries_with_fingerprint_on_risk(self):
        calls = []

        def fake(url, params, cookie="", **kw):
            calls.append(dict(params))
            if len(calls) == 1:
                raise C.BiliError("接口错误 code=-352", code=-352)
            return self.PAYLOAD

        with mock.patch.object(C, "ensure_buvid", return_value="buvid3=1") as buv, \
             mock.patch.object(C, "wbi_get", side_effect=fake):
            out = run("up_info", mid=2)
        buv.assert_called_once()                         # 没 buvid3 时补一个指纹
        self.assertEqual(len(calls), 2)                  # 先最小参数，再补 dm_* 重试
        self.assertEqual(calls[0], {"mid": 2})
        self.assertIn("dm_img_list", calls[1])
        self.assertEqual(calls[1]["mid"], 2)
        self.assertEqual(out["name"], "碧诗")

    def test_retries_once_when_account_not_logged_in(self):
        calls = []

        def fake(url, params, cookie="", **kw):
            calls.append(dict(params))
            if len(calls) == 1:
                raise C.BiliError("账号未登录", code=-101, kind="auth")
            return self.PAYLOAD

        with mock.patch.object(C, "ensure_buvid", return_value=""), \
             mock.patch.object(C, "wbi_get", side_effect=fake):
            out = run("up_info", mid=2)
        self.assertEqual(len(calls), 2)
        self.assertIn("dm_cover_img_str", calls[1])
        self.assertEqual(out["name"], "碧诗")

    def test_other_error_not_retried(self):
        calls = []

        def fake(url, params, cookie="", **kw):
            calls.append(url)
            raise C.BiliError("不存在", code=-404, kind="not_found")

        with mock.patch.object(C, "ensure_buvid", return_value=""), \
             mock.patch.object(C, "wbi_get", side_effect=fake):
            with self.assertRaises(C.BiliError):
                run("up_info", mid=2)
        self.assertEqual(len(calls), 1)

    def test_empty_data_raises_not_found(self):
        with mock.patch.object(C, "ensure_buvid", return_value=""), \
             mock.patch.object(C, "wbi_get", Recorder({"code": 0, "data": None})):
            with self.assertRaises(C.BiliError) as cm:
                run("up_info", mid=2)
        self.assertEqual(cm.exception.kind, "not_found")


class UpVideosTest(unittest.TestCase):
    VLIST = [{"bvid": "BV1x", "aid": 5, "title": "投稿1", "author": "碧诗",
              "created": 1700000000, "length": "03:20", "play": 10,
              "video_review": 1, "comment": 2, "description": "d", "pic": "p.jpg"}]

    def test_reads_vlist_inside_list_object(self):
        payload = {"code": 0, "data": {"list": {"slist": [], "tlist": [], "vlist": self.VLIST},
                                       "page": {"count": 42, "pn": 1, "ps": 20}}}
        rec = Recorder(payload)
        with mock.patch.object(C, "ensure_buvid", return_value=""), \
             mock.patch.object(C, "wbi_get", rec):
            out = run("up_videos", mid=2, page_size=20)
        self.assertIn("/x/space/wbi/arc/search", rec.urls[0])
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["total"], 42)
        self.assertEqual(out["author"], "碧诗")
        it = out["items"][0]
        self.assertEqual(it["duration"], "03:20")
        self.assertEqual(it["pubdate"], "2023-11-15 06:13")
        self.assertEqual(it["url"], "https://www.bilibili.com/video/BV1x")

    def test_tolerates_plain_list(self):
        payload = {"code": 0, "data": {"list": self.VLIST, "page": {"count": 1}}}
        with mock.patch.object(C, "ensure_buvid", return_value=""), \
             mock.patch.object(C, "wbi_get", Recorder(payload)):
            out = run("up_videos", mid=2)
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["total"], 1)

    def test_empty_data_raises(self):
        with mock.patch.object(C, "ensure_buvid", return_value=""), \
             mock.patch.object(C, "wbi_get", Recorder({"code": 0, "data": {}})):
            with self.assertRaises(C.BiliError) as cm:
                run("up_videos", mid=2)
        self.assertEqual(cm.exception.kind, "not_found")


# ───────────────────────── 番剧 ─────────────────────────

class SeasonTest(unittest.TestCase):
    def test_reads_result_field(self):
        rec = Recorder(SEASON)
        with mock.patch.object(C, "get_json", rec):
            out = run("season", season_id=47836)
        self.assertIn("/pgc/view/web/season?season_id=47836", rec.urls[0])
        self.assertEqual(out["title"], "鬼灭之刃 柱训练篇")
        self.assertEqual(out["total"], 8)
        self.assertEqual(out["status"], "已完结, 全8话")
        self.assertTrue(out["finished"])
        self.assertEqual(out["pub_time"], "2024-06-22 20:00")
        self.assertEqual(out["rating"]["score"], 9.7)
        self.assertEqual(out["stats"]["view"], 265200354)
        self.assertEqual(out["areas"], ["日本"])
        self.assertEqual(out["up"]["name"], "哔哩哔哩番剧")
        ep = out["episodes"][0]
        self.assertEqual(ep["duration"], 2938)          # 毫秒 → 秒
        self.assertEqual(ep["duration_hms"], "48:58")
        self.assertEqual(ep["pubdate"], "2024-06-22 20:00")
        self.assertEqual(ep["url"], "https://www.bilibili.com/bangumi/play/ep826497")

    def test_by_ep_id(self):
        rec = Recorder(SEASON)
        with mock.patch.object(C, "get_json", rec):
            out = run("season", ep_id=826497)
        self.assertIn("ep_id=826497", rec.urls[0])
        self.assertEqual(out["season_id"], 47836)        # 服务端会带回 season_id

    def test_requires_one_of_ids(self):
        with self.assertRaises(C.BiliError):
            run("season")

    def test_missing_raises(self):
        with mock.patch.object(C, "get_json", Recorder({"code": -404, "result": None})):
            with self.assertRaises(C.BiliError) as cm:
                run("season", season_id=1)
        self.assertEqual(cm.exception.kind, "not_found")


# ───────────────────────── 动态 ─────────────────────────

class DynamicTest(unittest.TestCase):
    def test_parses_archive_and_word(self):
        rec = Recorder(DYN)
        with mock.patch.object(C, "ensure_buvid", return_value="buvid3=1"), \
             mock.patch.object(C, "get_json", rec):
            out = run("dynamic", mid=2)
        url = rec.urls[0]
        self.assertIn("/x/polymer/web-dynamic/v1/feed/space?host_mid=2", url)
        self.assertIn("timezone_offset=-480", url)
        self.assertEqual(out["count"], 2)
        self.assertTrue(out["has_more"])
        self.assertEqual(out["offset"], "1163608706112290850")

        av = out["items"][0]
        self.assertEqual(av["kind"], "视频")
        self.assertEqual(av["title"], "视频标题")
        self.assertEqual(av["bvid"], "BV1x")
        self.assertEqual(av["url"], "https://www.bilibili.com/video/BV1x")
        self.assertEqual(av["time"], "2023-11-15 06:13")
        self.assertEqual(av["stats"], {"like": 10, "comment": 2, "forward": 1})

        word = out["items"][1]
        self.assertEqual(word["kind"], "动态")
        self.assertEqual(word["title"], "纯文字动态内容")   # 没标题就用正文首段
        self.assertEqual(word["url"], "https://t.bilibili.com/2")
        self.assertEqual(word["time"], "2024-01-01 12:00")  # 字符串时间原样截到分钟

    def test_limit(self):
        with mock.patch.object(C, "ensure_buvid", return_value=""), \
             mock.patch.object(C, "get_json", Recorder(DYN)):
            out = run("dynamic", mid=2, limit=1)
        self.assertEqual(out["count"], 1)

    def test_long_text_truncated_with_ellipsis(self):
        long_text = "很长的动态正文" * 30
        payload = {"code": 0, "data": {"items": [
            {"id_str": "9", "type": "DYNAMIC_TYPE_WORD",
             "modules": {"module_author": {"name": "UP", "mid": 2},
                         "module_dynamic": {"desc": {"text": long_text}}}}]}}
        with mock.patch.object(C, "ensure_buvid", return_value=""), \
             mock.patch.object(C, "get_json", Recorder(payload)):
            out = run("dynamic", mid=2)
        it = out["items"][0]
        self.assertEqual(len(it["title"]), 60)
        self.assertTrue(it["title"].endswith("…"))
        self.assertEqual(len(it["text"]), 140)
        self.assertTrue(it["text"].endswith("…"))

    def test_risk_maps_to_risk_kind(self):
        with mock.patch.object(C, "ensure_buvid", return_value=""), \
             mock.patch.object(C, "get_json",
                               side_effect=C.BiliError("x", code=-352)):
            with self.assertRaises(C.BiliError) as cm:
                run("dynamic", mid=2)
        self.assertEqual(cm.exception.kind, "risk")
        self.assertEqual(cm.exception.code, -352)

    def test_other_error_passthrough(self):
        with mock.patch.object(C, "ensure_buvid", return_value=""), \
             mock.patch.object(C, "get_json",
                               side_effect=C.BiliError("x", code=-404, kind="not_found")):
            with self.assertRaises(C.BiliError) as cm:
                run("dynamic", mid=2)
        self.assertEqual(cm.exception.kind, "not_found")


if __name__ == "__main__":
    unittest.main()
