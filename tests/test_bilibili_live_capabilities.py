# -*- coding: utf-8 -*-
"""联网验收：把「全部能力」在真实网络上跑一遍。

默认跳过，只有显式设置 CNREACH_LIVE=1 才会执行（与 test_bilibili_live.py 口径一致）：
    CNREACH_LIVE=1 python -m unittest tests.test_bilibili_live_capabilities -v

分两组：
  L1 公开能力 —— 不需要 cookie，国内出口即可跑通（13 项）
  L2 账号能力 —— 需要有效 cookie，没有 cookie 时整组跳过（8 项）

能力面按「只读、本人可访问、不做全量采集」收窄过一轮：playurl / danmaku / online /
region / article / live_room / fav / relation_stat / followers 已移除，不再验收。

注意：这是验收用例，不是 CI 用例。B站出口 IP 容易踩 -412，CI 不跑它。
"""
from __future__ import annotations

import os
import unittest

from adapters import get_adapter
from adapters.bilibili.client import BiliError

LIVE = os.environ.get("CNREACH_LIVE") == "1"
SAMPLE_BV = "BV1tXx56WECd"


@unittest.skipUnless(LIVE, "联网验收：设置 CNREACH_LIVE=1 才跑")
class LiveCapabilityBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ad = get_adapter("bilibili")
        try:
            cls.doctor = cls.ad.doctor()
        except BiliError as e:                     # 极端情况下 doctor 自己抛错
            raise unittest.SkipTest(f"doctor 异常：{e.kind} {e}")

    def call(self, name, **kwargs):
        """调一个能力，断言通用契约（返回 dict 且带 platform）。"""
        out = self.ad.call(name, **kwargs)
        self.assertIsInstance(out, dict, f"{name} 必须返回 dict")
        self.assertEqual(out.get("platform"), "bilibili", f"{name} 缺少 platform 字段")
        return out

    def assert_nonempty_list(self, out, key, name):
        self.assertIn(key, out, f"{name} 输出缺少 {key} 字段")
        self.assertIsInstance(out[key], list, f"{name}.{key} 必须是 list")


class LiveL1Capabilities(LiveCapabilityBase):
    """L1 公开能力：不需要登录态。"""

    def test_01_search(self):
        out = self.call("search", keyword="原神", limit=5)
        self.assert_nonempty_list(out, "items", "search")
        self.assertTrue(out["items"], "搜索结果为空（可能是风控或网络问题）")

    def test_02_video(self):
        out = self.call("video", target=SAMPLE_BV)
        self.assertTrue(out.get("title"), "video 缺 title")
        self.assertTrue(out.get("bvid") or out.get("aid"), "video 缺 bvid/aid")

    def test_03_pages(self):
        out = self.call("pages", target=SAMPLE_BV)
        self.assert_nonempty_list(out, "items", "pages")
        self.assertTrue(out["items"][0].get("cid"), "pages 缺 cid")

    def test_04_comments(self):
        out = self.call("comments", target=SAMPLE_BV, limit=5)
        self.assert_nonempty_list(out, "items", "comments")

    def test_05_replies(self):
        # 楼中楼：不给 root 时应自动挑一条有回复的热评；随机视频可能挑不到，允许空但要成功返回
        out = self.call("replies", target=SAMPLE_BV, limit=5)
        self.assert_nonempty_list(out, "items", "replies")
        if out["items"]:
            item = out["items"][0]
            self.assertTrue(item.get("text") or item.get("content"), "replies 条目缺正文")

    def test_06_related(self):
        out = self.call("related", target=SAMPLE_BV, limit=5)
        self.assert_nonempty_list(out, "items", "related")

    def test_07_ranking(self):
        out = self.call("ranking", limit=5)
        self.assert_nonempty_list(out, "items", "ranking")
        self.assertTrue(out["items"], "排行榜为空")

    def test_08_popular(self):
        out = self.call("popular", page_size=5)
        self.assert_nonempty_list(out, "items", "popular")

    def test_09_up_info(self):
        out = self.call("up_info", mid=2)
        self.assertTrue(out.get("name"), "up_info 缺 name")

    def test_10_up_videos(self):
        out = self.call("up_videos", mid=2, page_size=5)
        self.assert_nonempty_list(out, "items", "up_videos")

    def test_11_season(self):
        # 番剧：鬼灭之刃（season_id 固定）
        out = self.call("season", season_id=28747)
        self.assertTrue(out.get("title"), "season 缺 title")

    def test_12_dynamic(self):
        out = self.call("dynamic", mid=2, limit=5)
        self.assert_nonempty_list(out, "items", "dynamic")

    def test_13_subtitle(self):
        # 字幕必须登录态才给 subtitle_url；没 cookie 时允许走「简介+热评」兜底
        out = self.call("subtitle", target=SAMPLE_BV, max_chars=1000)
        self.assertIn("available", out, "subtitle 缺 available（区分真字幕 / 兜底）")
        if out["available"]:
            self.assertTrue(out.get("subtitle") or out.get("body"), "有字幕却没有内容")
        else:
            self.assertTrue(out.get("fallback"), "无字幕时必须给出 fallback")


@unittest.skipUnless(LIVE, "联网验收：设置 CNREACH_LIVE=1 才跑")
class LiveL2Capabilities(LiveCapabilityBase):
    """L2 账号能力：需要有效 cookie，没有就整组跳过。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not getattr(cls, "doctor", {}).get("has_cookie"):
            raise unittest.SkipTest("没有 cookie，跳过账号类能力验收")
        if not cls.doctor.get("logged_in"):
            raise unittest.SkipTest(f"cookie 未登录：{cls.doctor.get('error')}")

    def test_01_me(self):
        out = self.call("me")
        self.assertTrue(out.get("logged_in"), "me 显示未登录")
        self.assertTrue(out.get("name"), "me 缺 name")

    def test_02_history(self):
        out = self.call("history", page_size=5)
        self.assert_nonempty_list(out, "items", "history")

    def test_03_toview(self):
        out = self.call("toview")
        self.assert_nonempty_list(out, "items", "toview")

    def test_04_my_folders(self):
        out = self.call("my_folders")
        self.assert_nonempty_list(out, "items", "my_folders")

    def test_05_followings(self):
        out = self.call("followings", page_size=5)
        self.assert_nonempty_list(out, "items", "followings")

    def test_06_my_dynamic(self):
        out = self.call("my_dynamic", limit=5)
        self.assert_nonempty_list(out, "items", "my_dynamic")

    def test_08_liked(self):
        # 已赞查询：data=1 已赞 / 0 未赞，两种都合法，只验形状与布尔类型
        out = self.call("liked", target=SAMPLE_BV)
        self.assertEqual(out["count"], 1, "liked 应只查一个 BV")
        it = out["items"][0]
        self.assertEqual(it["id"], SAMPLE_BV)
        self.assertIsInstance(it["liked"], bool, "liked 必须是布尔值")
        self.assertIsInstance(out["liked_ids"], list, "liked_ids 必须是 list")
        self.assertEqual(out["liked_count"], len(out["liked_ids"]))

    def test_07_my_bangumi(self):
        out = self.call("my_bangumi", page_size=5)
        self.assert_nonempty_list(out, "items", "my_bangumi")


if __name__ == "__main__":
    unittest.main()
