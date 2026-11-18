# -*- coding: utf-8 -*-
"""L2 登录态能力的离线单测 —— 全部 mock 掉 client.get_json，不联网、不需要 cookie。

覆盖：8 个能力的归一化输出、mid 从 nav 取、游标翻页、以及「无 cookie 时一个请求都不发」。
"""
import io
import urllib.parse
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from adapters.bilibili import cli
from adapters.bilibili import client
from adapters.bilibili.adapter import BilibiliAdapter

# ───────────────────────── 假响应 ─────────────────────────

NAV = {
    "code": 0,
    "data": {
        "isLogin": True, "mid": 4242, "uname": "阿米娅", "money": 12,
        "level_info": {"current_level": 5, "current_exp": 12345},
        "vip": {"status": 1, "type": 2, "label": {"text": "年度大会员"}},
        "face": "https://i0.hdslb.com/bfs/face/a.jpg",
    },
}

HISTORY_P1 = {
    "code": 0,
    "data": {
        "cursor": {"max": 1001, "view_at": 1700000000, "business": "archive", "ps": 20},
        "tab": [],
        "list": [{
            "title": "视频一", "author_name": "UP甲", "author_mid": 11,
            "view_at": 1700000000, "progress": -1, "current": 30, "duration": 300,
            "history": {"oid": 111, "bvid": "BV1aa411c7mD", "business": "archive"},
        }],
    },
}

HISTORY_P2 = {
    "code": 0,
    "data": {
        "cursor": {"max": 900, "view_at": 1600000000, "business": "archive", "ps": 20},
        "list": [{
            "title": "直播回放", "author_name": "UP乙", "author_mid": 22,
            "view_at": 1600000000, "progress": 12, "duration": 60,
            "history": {"oid": 222, "business": "live"},
        }],
    },
}

TOVIEW = {
    "code": 0,
    "data": {
        "count": 1,
        "list": [{
            "bvid": "BV1bb411c7mD", "title": "稍后看一",
            "owner": {"name": "UP丙"}, "duration": 120, "progress": 60,
            "add_at": 1700000000,
        }],
    },
}

FOLDERS = {
    "code": 0,
    "data": {
        "count": 2,
        "list": [
            {"id": 1001, "fid": 1001, "title": "默认收藏夹", "attr": 0,
             "media_count": 42, "mid": 4242},
            {"id": 2002, "fid": 2002, "title": "私密夹", "attr": 1,
             "media_count": 3, "mid": 4242},
        ],
    },
}

FOLLOWINGS = {
    "code": 0,
    "data": {
        "total": 2, "re_version": 0,
        "list": [{"mid": 501, "uname": "关注甲", "sign": "签名"},
                 {"mid": 502, "uname": "关注乙", "sign": ""}],
    },
}

FOLLOWERS = {
    "code": 0,
    "data": {
        "total": 1, "re_version": 0,
        "list": [{"mid": 601, "uname": "粉丝甲", "sign": "你好"}],
    },
}

DYNAMIC = {
    "code": 0,
    "data": {
        "offset": "dyn-off-2", "has_more": True, "update_num": 3,
        "items": [
            {"id_str": "9001", "type": "DYNAMIC_TYPE_AV",
             "modules": {
                 "module_author": {"mid": 4242, "name": "阿米娅", "pub_ts": 1700000000},
                 "module_dynamic": {
                     "desc": {"text": "新视频来啦"},
                     "major": {"archive": {"bvid": "BV1cc411c7mD", "title": "投稿标题"}},
                 },
             }},
            {"id_str": "9002", "type": "DYNAMIC_TYPE_DRAW",
             "modules": {
                 "module_author": {"mid": 4242, "name": "阿米娅", "pub_ts": 1700000100},
                 "module_dynamic": {"desc": {"text": "随手拍"}},
             }},
        ],
    },
}

BANGUMI = {
    "code": 0,
    "data": {
        "total": 1, "pn": 1, "ps": 20,
        "list": [{
            "season_id": 33001, "media_id": 88001, "title": "番剧甲",
            "season_type_name": "番剧", "cover": "https://i0.hdslb.com/x.jpg",
            "new_ep": {"index_show": "更新至第 10 话"},
            "total_count": 12, "follow_status": 2,
        }],
    },
}

EMPTY_NAV = {"code": 0, "data": {"isLogin": False, "mid": 0, "uname": ""}}


class _Router:
    """按 URL 子串路由的假请求层；顺便把调用顺序记下来。"""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls = []

    def __call__(self, url, cookie="", **kw):
        self.calls.append(url)
        for key, val in self.routes.items():
            if key in url:
                return val(url, len(self.calls)) if callable(val) else val
        raise AssertionError(f"未预期的请求：{url}")

    def urls(self) -> str:
        return "\n".join(self.calls)


def _adapter(cookie: str = "SESSDATA=abc; buvid3=x") -> BilibiliAdapter:
    return BilibiliAdapter(cookie=cookie)


def _call(cap_name: str, routes: dict, **kwargs) -> tuple:
    """跑一个能力，返回 (结果, 路由记录)。"""
    router = _Router({"x/web-interface/nav": NAV, **routes})
    with mock.patch.object(client, "get_json", router):
        out = _adapter().call(cap_name, **kwargs)
    return out, router


# ───────────────────────── 1. me ─────────────────────────

class MeTest(unittest.TestCase):
    def test_me_normalizes_nav(self):
        out, router = _call("me", {})
        self.assertEqual(out["platform"], "bilibili")
        self.assertTrue(out["logged_in"])
        self.assertEqual(out["mid"], 4242)
        self.assertEqual(out["name"], "阿米娅")
        self.assertEqual(out["level"], 5)          # 取自 level_info.current_level
        self.assertTrue(out["vip"])
        self.assertEqual(out["vip_label"], "年度大会员")
        self.assertIn("x/web-interface/nav", router.calls[0])

    def test_me_reports_not_logged_in_without_raising(self):
        router = _Router({"x/web-interface/nav": EMPTY_NAV})
        with mock.patch.object(client, "get_json", router):
            out = _adapter().call("me")
        self.assertFalse(out["logged_in"])
        self.assertEqual(out["mid"], 0)
        self.assertFalse(out["name"])      # 未登录时 nav 给的是空串，如实透出


# ───────────────────────── 2. history ─────────────────────────

class HistoryTest(unittest.TestCase):
    def test_history_first_page(self):
        out, router = _call("history", {"history/cursor": HISTORY_P1})
        self.assertEqual(out["count"], 1)
        it = out["items"][0]
        self.assertEqual(it["id"], "BV1aa411c7mD")
        self.assertEqual(it["title"], "视频一")
        self.assertEqual(it["author"], "UP甲")
        self.assertEqual(it["url"], "https://www.bilibili.com/video/BV1aa411c7mD")
        self.assertEqual(it["business"], "archive")
        self.assertEqual(it["current"], 30)
        self.assertEqual(it["viewed_at"], "2023-11-15 06:13")   # 北京时间，不随本机时区变
        self.assertIn("ps=20", router.calls[0])

    def test_history_page_two_walks_cursor(self):
        pages = {1: HISTORY_P1, 2: HISTORY_P2}
        out, router = _call("history", {"history/cursor": lambda url, n: pages[n]}, page=2)
        # 取第 2 页要翻两次游标：第二次必须带上第一页返回的 cursor.max
        self.assertEqual(len(router.calls), 2)
        self.assertIn("max=0", router.calls[0])
        self.assertIn("max=1001", router.calls[1])
        self.assertEqual(out["page"], 2)
        self.assertEqual(out["items"][0]["title"], "直播回放")

    def test_history_page_size_clamped(self):
        out, router = _call("history", {"history/cursor": {"code": 0, "data": {}}},
                            page=1, page_size=500)
        self.assertEqual(out["page_size"], 50)
        self.assertEqual(out["count"], 0)


# ───────────────────────── 3. toview ─────────────────────────

class ToViewTest(unittest.TestCase):
    def test_toview_normalizes(self):
        out, router = _call("toview", {"x/v2/history/toview": TOVIEW})
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["total"], 1)
        it = out["items"][0]
        self.assertEqual(it["id"], "BV1bb411c7mD")
        self.assertEqual(it["author"], "UP丙")
        self.assertEqual(it["duration"], 120)
        self.assertEqual(it["added_at"], "2023-11-15 06:13")
        self.assertIn("x/v2/history/toview", router.calls[0])


# ───────────────────────── 4. my_folders ─────────────────────────

class MyFoldersTest(unittest.TestCase):
    def test_folders_use_mid_from_nav(self):
        out, router = _call("my_folders", {"fav/folder/created/list-all": FOLDERS})
        self.assertEqual(out["mid"], 4242)
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["total"], 2)
        self.assertIn("up_mid=4242", router.calls[-1])   # mid 由 nav 推导，不是用户传入
        first = out["items"][0]
        self.assertEqual(first["id"], 1001)
        self.assertEqual(first["title"], "默认收藏夹")
        self.assertEqual(first["media_count"], 42)
        self.assertFalse(first["private"])
        self.assertTrue(out["items"][1]["private"])      # attr=1 → 私密
        self.assertIn("favlist?fid=1001", first["url"])

    def test_folders_empty_list_is_not_error(self):
        out, _ = _call("my_folders", {"fav/folder/created/list-all": {"code": 0, "data": None}})
        self.assertEqual(out["count"], 0)
        self.assertEqual(out["items"], [])


# ───────────────────────── 5/6. followings / followers ─────────────────────────

class RelationTest(unittest.TestCase):
    def test_followings(self):
        out, router = _call("followings", {"relation/followings": FOLLOWINGS},
                            page=1, page_size=20)
        self.assertEqual(out["mid"], 4242)
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["total"], 2)
        self.assertEqual(out["items"][0]["name"], "关注甲")
        self.assertEqual(out["items"][0]["url"], "https://space.bilibili.com/501")
        last = router.calls[-1]
        self.assertIn("vmid=4242", last)
        self.assertIn("pn=1", last)
        self.assertIn("ps=20", last)

    def test_followings_passes_page(self):
        out, router = _call("followings", {"relation/followings": FOLLOWINGS},
                            page=3, page_size=10)
        self.assertIn("pn=3", router.calls[-1])
        self.assertIn("ps=10", router.calls[-1])
        self.assertEqual(out["page"], 3)

    def test_followers(self):
        out, router = _call("followers", {"relation/followers": FOLLOWERS})
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["items"][0]["name"], "粉丝甲")
        self.assertIn("relation/followers", router.calls[-1])


# ───────────────────────── 7. my_dynamic ─────────────────────────

class MyDynamicTest(unittest.TestCase):
    def test_dynamic_uses_wbi_and_normalizes(self):
        router = _Router({
            "x/web-interface/nav": NAV,
            "polymer/web-dynamic/v1/feed/all": DYNAMIC,
        })
        with mock.patch.object(client, "get_json", router), \
             mock.patch.object(client, "wbi_mix_cached", return_value="MIX"):
            out = _adapter().call("my_dynamic", offset="dyn-off-1", limit=5)
        url = router.calls[-1]
        self.assertIn("/x/polymer/web-dynamic/v1/feed/all", url)
        self.assertIn("w_rid=", url)                  # 必须走 wbi 签名
        self.assertIn("offset=dyn-off-1", url)
        self.assertIn("limit=5", url)
        self.assertEqual(out["count"], 2)
        self.assertTrue(out["has_more"])
        self.assertEqual(out["offset"], "dyn-off-2")
        av = out["items"][0]
        self.assertEqual(av["id"], "9001")
        self.assertEqual(av["type"], "DYNAMIC_TYPE_AV")
        self.assertEqual(av["url"], "https://www.bilibili.com/video/BV1cc411c7mD")
        self.assertEqual(av["text"], "新视频来啦")
        self.assertEqual(av["published_at"], "2023-11-15 06:13")
        draw = out["items"][1]
        self.assertEqual(draw["url"], "https://space.bilibili.com/4242")
        self.assertEqual(draw["text"], "随手拍")


# ───────────────────────── 8. my_bangumi ─────────────────────────

class MyBangumiTest(unittest.TestCase):
    def test_bangumi_normalizes(self):
        out, router = _call("my_bangumi", {"space/bangumi/follow/list": BANGUMI})
        self.assertEqual(out["count"], 1)
        it = out["items"][0]
        self.assertEqual(it["season_id"], 33001)
        self.assertEqual(it["title"], "番剧甲")
        self.assertEqual(it["type"], "番剧")
        self.assertEqual(it["progress"], "更新至第 10 话")
        self.assertEqual(it["url"], "https://www.bilibili.com/bangumi/play/ss33001")
        last = router.calls[-1]
        self.assertIn("vmid=4242", last)
        self.assertIn("type=1", last)

    def test_bangumi_empty_is_not_error(self):
        out, _ = _call("my_bangumi", {"space/bangumi/follow/list": {"code": 0, "data": {"total": 0, "list": None}}})
        self.assertEqual(out["count"], 0)


# ───────────────────────── 无 cookie：不发请求 ─────────────────────────

AUTH_CAPS = ("me", "history", "toview", "my_folders",
             "followings", "followers", "my_dynamic", "my_bangumi")


class NoCookieTest(unittest.TestCase):
    def test_all_auth_capabilities_refuse_without_request(self):
        for name in AUTH_CAPS:
            with mock.patch.object(client, "get_json") as gj:
                with self.assertRaises(client.BiliError) as cm:
                    _adapter(cookie="").call(name)
            self.assertEqual(cm.exception.kind, "auth", name)
            gj.assert_not_called()      # 关键：没有 cookie 连 nav 都不该打

    def test_all_eight_registered_as_auth(self):
        from adapters.bilibili.capabilities import CAPABILITIES
        for name in AUTH_CAPS:
            self.assertIn(name, CAPABILITIES)
            self.assertTrue(CAPABILITIES[name].auth, name)


# ───────────────────────── CLI 接线 ─────────────────────────

class _FakeAdapter:
    """替身：记录 CLI 传下来的 kwargs，不发请求。"""

    calls: list = []

    def __init__(self, *a, **kw):
        pass

    def call(self, name, **kwargs):
        _FakeAdapter.calls.append((name, kwargs))
        return {"platform": "bilibili", "name": name}

    def doctor(self):
        return {"platform": "bilibili", "ok": True}


class CliAccountTest(unittest.TestCase):
    def setUp(self):
        _FakeAdapter.calls = []

    def _run(self, argv):
        buf = io.StringIO()
        with mock.patch.object(cli, "BilibiliAdapter", _FakeAdapter), \
             redirect_stdout(buf):
            rc = cli.main(argv)
        return rc, buf.getvalue()

    def test_cli_maps_dashed_flags_to_kwargs(self):
        rc, out = self._run(["history", "--page", "2", "--page-size", "5"])
        self.assertEqual(rc, 0)
        self.assertEqual(_FakeAdapter.calls[0][0], "history")
        self.assertEqual(_FakeAdapter.calls[0][1], {"page": 2, "page_size": 5})
        self.assertEqual(out.strip().splitlines()[0], "{")

    def test_cli_offset_defaults_to_empty_string(self):
        rc, _ = self._run(["my_dynamic", "--limit", "5"])
        self.assertEqual(rc, 0)
        self.assertEqual(_FakeAdapter.calls[0][1], {"limit": 5})

    def test_cli_bili_error_exits_2(self):
        class _Boom(_FakeAdapter):
            def call(self, name, **kwargs):
                raise client.BiliError("被风控", kind="risk")

        err = io.StringIO()
        with mock.patch.object(cli, "BilibiliAdapter", _Boom), \
             redirect_stdout(io.StringIO()), redirect_stderr(err):
            rc = cli.main(["toview"])
        self.assertEqual(rc, 2)
        self.assertIn("[risk]", err.getvalue())


if __name__ == "__main__":
    unittest.main()
