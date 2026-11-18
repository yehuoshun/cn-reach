"""B站扫码登录模块的单元测试 —— 全部离线（mock 掉网络与二维码渲染）。

运行：
    python -m unittest discover -s tests -t .
"""
import os
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from adapters.bilibili import client as C
from adapters.bilibili import login as L


class _Cookie:
    def __init__(self, name, value):
        self.name = name
        self.value = value


class ParseArgsTest(unittest.TestCase):
    def test_defaults(self):
        timeout, qr_dir = L._parse_args([])
        self.assertEqual(timeout, L.POLL_SECONDS)
        self.assertEqual(qr_dir, C.workspace_root() / "tmp")

    def test_custom_values(self):
        timeout, qr_dir = L._parse_args(["--timeout", "30", "--qr-dir", "D:/q"])
        self.assertEqual(timeout, 30.0)
        self.assertEqual(qr_dir, Path("D:/q"))

    def test_ignores_garbage(self):
        timeout, _ = L._parse_args(["--timeout", "abc", "--nope", "x"])
        self.assertEqual(timeout, L.POLL_SECONDS)


class CollectAuthTest(unittest.TestCase):
    def test_from_jar(self):
        jar = [_Cookie("SESSDATA", "s"), _Cookie("bili_jct", "j"),
               _Cookie("DedeUserID", "1"), _Cookie("DedeUserID__ckMd5", "m")]
        auth = L._collect_auth("", jar)
        self.assertEqual(auth["SESSDATA"], "s")
        self.assertEqual(auth["DedeUserID__ckMd5"], "m")

    def test_falls_back_to_gourl_query(self):
        inner = "SESSDATA=s2&bili_jct=j2&DedeUserID=2"
        target = "https://www.bilibili.com/?gourl=" + urllib.parse.quote(inner, safe="")
        auth = L._collect_auth(target, None)
        self.assertEqual(auth["SESSDATA"], "s2")
        self.assertEqual(auth["bili_jct"], "j2")
        self.assertEqual(auth["DedeUserID"], "2")

    def test_empty_when_nothing(self):
        auth = L._collect_auth("", None)
        self.assertFalse(any(auth.values()))


class MaskTest(unittest.TestCase):
    def test_mask_hides_value(self):
        out = L._mask("SESSDATAabcd")
        self.assertTrue(out.startswith("SESS…") or out.startswith("SESS"))
        self.assertIn("字符", out)
        self.assertEqual(L._mask(""), "空")


class WriteCookieTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "config" / "bilibili.txt"
        patch = mock.patch.object(C, "cookie_path", lambda: self.path)
        patch.start()
        self.addCleanup(patch.stop)

    def test_creates_file_and_parent(self):
        out = L._write_cookie({"SESSDATA": "s", "bili_jct": "j", "DedeUserID": "1"})
        self.assertEqual(out, self.path)
        text = self.path.read_text(encoding="utf-8").strip()
        self.assertIn("SESSDATA=s", text)
        self.assertIn("bili_jct=j", text)

    def test_replaces_only_auth_keys_and_keeps_others(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("SESSDATA=old; bili_jct=oldj; buvid3=keep\n", encoding="utf-8")
        L._write_cookie({"SESSDATA": "new", "bili_jct": "newj", "DedeUserID": "9"})
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("SESSDATA=new", text)
        self.assertIn("bili_jct=newj", text)
        self.assertIn("DedeUserID=9", text)
        self.assertIn("buvid3=keep", text)
        self.assertNotIn("SESSDATA=old", text)

    def test_backs_up_previous_file(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("SESSDATA=old\n", encoding="utf-8")
        L._write_cookie({"SESSDATA": "new"})
        backup = self.path.with_name(self.path.name + ".bak-qr")
        self.assertTrue(backup.is_file())
        self.assertIn("SESSDATA=old", backup.read_text(encoding="utf-8"))


class MainFlowTest(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(L, "POLL_INTERVAL", 0)
        patch.start()
        self.addCleanup(patch.stop)

    def test_generate_failure_returns_1(self):
        with mock.patch.object(L, "_generate", side_effect=C.BiliError("boom")):
            self.assertEqual(L.main([]), L.EXIT_FAIL)

    def test_missing_qrcode_dependency_returns_4(self):
        with mock.patch.object(L, "_generate", return_value=("https://x/y", "key")), \
             mock.patch.object(L, "_save_qr", side_effect=ImportError):
            self.assertEqual(L.main([]), L.EXIT_MISSING_DEP)

    def test_expired_qr_returns_2(self):
        with mock.patch.object(L, "_generate", return_value=("https://x/y", "key")), \
             mock.patch.object(L, "_save_qr", return_value=Path("qr.png")), \
             mock.patch.object(L, "_http_json", return_value={"code": 0, "data": {"code": -5}}):
            self.assertEqual(L.main(["--timeout", "5"]), L.EXIT_EXPIRED)

    def test_success_delegates_to_finish(self):
        payload = {"code": 0, "data": {"code": 0, "url": "https://passport/x?a=1"}}
        with mock.patch.object(L, "_generate", return_value=("https://x/y", "key")), \
             mock.patch.object(L, "_save_qr", return_value=Path("qr.png")), \
             mock.patch.object(L, "_http_json", return_value=payload), \
             mock.patch.object(L, "_finish", return_value=L.EXIT_OK) as finish:
            self.assertEqual(L.main(["--timeout", "5"]), L.EXIT_OK)
            finish.assert_called_once_with("https://passport/x?a=1")

    def test_poll_timeout_returns_3(self):
        with mock.patch.object(L, "_generate", return_value=("https://x/y", "key")), \
             mock.patch.object(L, "_save_qr", return_value=Path("qr.png")), \
             mock.patch.object(L, "_http_json", return_value={"code": 0, "data": {"code": -2}}):
            self.assertEqual(L.main(["--timeout", "0"]), L.EXIT_TIMEOUT)


if __name__ == "__main__":
    unittest.main()
