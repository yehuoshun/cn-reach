"""B站适配器「联网」冒烟测试 —— 默认跳过，设 CNREACH_LIVE=1 才跑。

    CNREACH_LIVE=1 python -m unittest tests.test_bilibili_live -v

需要本机有有效 cookie（默认 ~/.openclaw/secrets/bilibili-cookies.txt）。
"""
import os
import unittest

from adapters.bilibili.adapter import BilibiliAdapter

LIVE = os.environ.get("CNREACH_LIVE") == "1"
SAMPLE_BV = "BV1tXx56WECd"   # 一个已存在的公开视频


@unittest.skipUnless(LIVE, "设 CNREACH_LIVE=1 才跑联网测试")
class LiveSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ad = BilibiliAdapter()
        # 出口 IP 被风控（-412 / v_voucher）或不通时，整组跳过而不是报错。
        # GitHub runner 是海外机房 IP，几乎必然被 B站 挡；本机（国内出口）才跑得动。
        probe = cls.ad.doctor()
        if not probe.get("ok"):
            if probe.get("kind") in ("risk", "network"):
                raise unittest.SkipTest(
                    f"出口 IP 不可用（{probe.get('kind')}：{probe.get('error')}）——跳过联网冒烟")
            raise AssertionError(f"doctor 失败：{probe}")

    def test_doctor(self):
        out = self.ad.doctor()
        self.assertTrue(out["ok"], out)

    def test_search(self):
        out = self.ad.search("崩坏3", limit=3)
        self.assertGreater(out["count"], 0)
        self.assertTrue(out["items"][0]["id"].startswith("BV"))

    def test_video(self):
        out = self.ad.video(SAMPLE_BV)
        self.assertEqual(out["id"], SAMPLE_BV)
        self.assertTrue(out["title"])

    def test_comments(self):
        out = self.ad.comments(SAMPLE_BV, limit=3)
        self.assertGreaterEqual(out["count"], 1)

    def test_subtitle_or_fallback(self):
        out = self.ad.subtitles(SAMPLE_BV)
        # 要么有字幕，要么有兜底，不允许两者皆空
        self.assertTrue(out["available"] or out.get("fallback"))


if __name__ == "__main__":
    unittest.main()
