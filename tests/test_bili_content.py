# -*- coding: utf-8 -*-
"""content 能力层单测 —— 全部离线（mock 掉网络层），零第三方依赖。

运行：
    PYTHONPATH=. PYTHONUTF8=1 python -m unittest discover -s tests -t .

约定：
  - 只 patch client.get_json / client.wbi_get，不碰真网络；
  - 每个能力都要验证「返回体带 platform」和「参数透传」。
"""
import json
import unittest
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
                  "replies", "related"}


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


# ───────────────────────── 契约 ─────────────────────────


class ContractTest(unittest.TestCase):
    def test_seven_capabilities_declared(self):
        names = [c.name for c in content.CAPABILITIES]
        self.assertEqual(set(names), EXPECTED_NAMES)
        self.assertEqual(len(names), 7, "能力名不能重复")

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
             ])):
            content._pages(fake, SAMPLE_BV)
            content._search(fake, "关键词")


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


# ───────────────────────── 相关推荐 ─────────────────────────


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


if __name__ == "__main__":
    unittest.main()
