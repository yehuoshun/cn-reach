# -*- coding: utf-8 -*-
"""能力契约测试 —— 只做横向的通用断言，不绑定任何一个具体能力的返回结构。

这样 content / site / account 三个模块各自新增能力时，这里不用改也能一直守住底线：
  ① 能力名 / 说明 / handler 齐备且不重名
  ② catalog() 里不能漏出 URL 或端点字符串（对外只暴露「能力」）
  ③ 需要登录态的能力在没有 cookie 时抛 auth 错，且一个请求都不发
  ④ bind() 拒绝未知参数、必填缺失要报错
  ⑤ CLI 的 list 子命令能跑通（以及 doctor 体检不通过时退出码非零）
"""
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from adapters.bilibili import cli, client
from adapters.bilibili.adapter import BilibiliAdapter
from adapters.bilibili.capabilities import CAPABILITIES, catalog
from adapters.bilibili.capabilities.spec import Capability, param

NAME_RE = r"^[a-z][a-z0-9_]*$"


class RegistryContractTest(unittest.TestCase):
    def test_capabilities_are_well_formed(self):
        self.assertTrue(CAPABILITIES, "能力注册表不能为空")
        seen = []
        for name, cap in CAPABILITIES.items():
            with self.subTest(cap=name):
                self.assertIsInstance(cap, Capability)
                self.assertEqual(name, cap.name, "注册表的 key 必须等于 cap.name")
                self.assertRegex(cap.name, NAME_RE, "能力名要能直接当 CLI 子命令")
                self.assertTrue(str(cap.summary).strip(), "缺一句话说明")
                self.assertTrue(callable(cap.handler), "handler 不可调用")
                self.assertFalse(cap.mutating, "只读项目不允许 mutating 能力")
                for prm in cap.params:
                    self.assertRegex(prm.name, NAME_RE, "参数名要能直接当 --参数 用")
                    self.assertIsInstance(prm.type, type)
                    if prm.required:
                        self.assertIsNone(prm.default, "必填参数不该带默认值")
                seen.append(cap.name)
        self.assertEqual(len(seen), len(set(seen)), "能力名不能重复")

    def test_every_handler_matches_signature(self):
        """handler 至少要能吃 (adapter, **kwargs) 这种调用形态。"""
        import inspect
        for name, cap in CAPABILITIES.items():
            with self.subTest(cap=name):
                sig = inspect.signature(cap.handler)
                params = list(sig.parameters.values())
                self.assertTrue(params, f"{name}: handler 至少要收 adapter")
                var_kw = any(p.kind is p.VAR_KEYWORD for p in params)
                declared = {p.name for p in params
                            if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)}
                self.assertTrue(var_kw or declared, f"{name}: handler 收不了参数")
                for prm in cap.params:
                    self.assertTrue(var_kw or prm.name in declared,
                                    f"{name}: 声明了参数 {prm.name} 但 handler 不接")


class CatalogContractTest(unittest.TestCase):
    """对外文档只暴露能力，不暴露端点 —— 这条红线靠 catalog() 守住。"""

    BANNED = ("http://", "https://", "api.bilibili.com", "/x/", "wbi")

    def test_catalog_has_no_url_or_endpoint(self):
        blob = json.dumps(catalog(), ensure_ascii=False).lower()
        for bad in self.BANNED:
            with self.subTest(token=bad):
                self.assertNotIn(bad.lower(), blob)

    def test_catalog_shape(self):
        rows = catalog()
        self.assertIsInstance(rows, list)
        self.assertEqual({r["name"] for r in rows}, set(CAPABILITIES))
        for row in rows:
            self.assertIn("summary", row)
            self.assertIn("auth", row)
            self.assertIsInstance(row["auth"], bool)
            self.assertIsInstance(row["params"], list)
            for prm in row["params"]:
                self.assertEqual({"name", "type", "required", "default"} - set(prm), set(),
                                 "参数行结构不完整")


class AuthContractTest(unittest.TestCase):
    def test_auth_capabilities_refuse_without_cookie(self):
        auth_caps = [c for c in CAPABILITIES.values() if c.auth]
        self.assertTrue(auth_caps, "至少应该有一个需要登录态的能力")
        for cap in auth_caps:
            with self.subTest(cap=cap.name):
                with mock.patch.object(client, "get_json") as get_json, \
                     mock.patch.object(client, "wbi_get") as wbi_get:
                    with self.assertRaises(client.BiliError) as cm:
                        BilibiliAdapter(cookie="").call(cap.name)
                self.assertEqual(cm.exception.kind, "auth")
                get_json.assert_not_called()
                wbi_get.assert_not_called()

    def test_public_capabilities_do_not_require_cookie(self):
        """反过来：auth=False 的能力不该因为没 cookie 就被 auth 拦下。

        只断言「不是 auth 拦截」——空数据下归一化阶段抛别的错是允许的，
        这条用例不替各模块的解析逻辑背书。
        """
        for cap in [c for c in CAPABILITIES.values() if not c.auth]:
            with self.subTest(cap=cap.name):
                kwargs = {}
                for prm in cap.params:
                    if prm.required:
                        kwargs[prm.name] = {int: 1, str: "BV1tXx56WECd"}.get(prm.type)
                with mock.patch.object(client, "get_json",
                                      return_value={"code": 0, "data": {}}), \
                     mock.patch.object(client, "wbi_get",
                                      return_value={"code": 0, "data": {}}), \
                     mock.patch.object(client, "wbi_mix_cached", return_value="MIX"), \
                     mock.patch.object(client, "wbi_mix", return_value="MIX"), \
                     mock.patch.object(client, "wbi_query", return_value="q=1"):
                    try:
                        BilibiliAdapter(cookie="").call(cap.name, **kwargs)
                    except client.BiliError as exc:
                        self.assertNotEqual(exc.kind, "auth",
                                            f"{cap.name} 不该因为没 cookie 被拦")
                    except Exception:  # noqa: BLE001 —— 空数据下别的错与本契约无关
                        pass


class BindContractTest(unittest.TestCase):
    def test_unknown_param_rejected(self):
        for cap in CAPABILITIES.values():
            with self.subTest(cap=cap.name):
                with self.assertRaises(ValueError):
                    cap.bind(__no_such_param__=1)

    def test_missing_required_rejected(self):
        # 合成能力：不依赖任何模块当前是否已有必填参数
        cap = Capability(name="synthetic", summary="合成能力，仅用于契约测试",
                         handler=lambda ad, **kw: {}, params=(param("target", str, required=True),))
        with self.assertRaises(ValueError):
            cap.bind()
        self.assertEqual(cap.bind(target="BV1x")["target"], "BV1x")
        # 现有能力里凡是声明了必填参数的，也必须真的校验
        for real in CAPABILITIES.values():
            if any(p.required for p in real.params):
                with self.subTest(cap=real.name):
                    with self.assertRaises(ValueError):
                        real.bind()

    def test_defaults_and_types_applied(self):
        cap = Capability(name="synthetic2", summary="合成能力，仅用于契约测试",
                         handler=lambda ad, **kw: {},
                         params=(param("page", int, default=1),))
        self.assertEqual(cap.bind(), {"page": 1})
        self.assertEqual(cap.bind(page=3), {"page": 3})


class CliContractTest(unittest.TestCase):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_list_subcommand_works(self):
        rc, out, _ = self._run(["list"])
        self.assertEqual(rc, 0)
        rows = json.loads(out)
        self.assertIsInstance(rows, list)
        self.assertEqual({r["name"] for r in rows}, set(CAPABILITIES))
        for row in rows:
            self.assertIn("params", row)

    def test_unknown_subcommand_exits_2_and_lists_choices(self):
        rc, _, err = self._run(["definitely-not-a-capability"])
        self.assertEqual(rc, 2)
        self.assertIn("未知子命令", err)
        self.assertIn("可用子命令", err)

    def test_help_runs(self):
        rc, out, _ = self._run(["--help"])
        self.assertEqual(rc, 0)
        self.assertIn("list", out)

    def test_doctor_ok_false_exits_3(self):
        boom = client.BiliError("网络不通", kind="network")
        with mock.patch.object(client, "nav_info", side_effect=boom):
            rc, out, _ = self._run(["doctor"])
        self.assertEqual(rc, 3, "体检不通过必须是非零退出码")
        self.assertFalse(json.loads(out)["ok"])

    def test_doctor_ok_true_exits_0(self):
        with mock.patch.object(client, "nav_info",
                               return_value={"isLogin": True, "uname": "u"}), \
             mock.patch.object(client, "wbi_mix", return_value="MIX"):
            rc, out, _ = self._run(["doctor"])
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(out)["ok"])

    def test_legacy_positional_and_dash_flags(self):
        """README 里的旧写法：必填参数当位置参数，可选参数用 --名字 值。"""
        cap = Capability(
            name="synthetic3", summary="合成能力，仅用于契约测试",
            handler=lambda ad, **kw: {"platform": "bilibili", "kwargs": kw},
            params=(param("target", str, required=True),
                    param("max_chars", int, default=None),
                    param("sort", str, default="hot")),
        )
        captured = {}

        class _Fake:
            def __init__(self, *a, **kw):
                pass

            def call(self, name, **kwargs):
                captured["name"] = name
                captured["kwargs"] = kwargs
                return {"platform": "bilibili"}

        with mock.patch.object(cli, "_caps", return_value={"synthetic3": cap}), \
             mock.patch.object(cli, "BilibiliAdapter", _Fake), \
             redirect_stdout(io.StringIO()):
            rc = cli.main(["synthetic3", "BV1bU4y1s7Cr", "--max-chars", "3000",
                           "--sort", "time"])
        self.assertEqual(rc, 0)
        # target 走位置参数；max_chars 由 --max-chars 映射回来（下划线 ↔ 横线）
        self.assertEqual(captured["kwargs"],
                         {"target": "BV1bU4y1s7Cr", "max_chars": 3000, "sort": "time"})

    def test_legacy_name_resolution(self):
        """旧命令名和新能力名只差单复数时（subtitle / subtitles）要能对上。"""
        cap = Capability(name="subtitles", summary="字幕", handler=lambda ad, **kw: {},
                         params=())
        caps = {"subtitles": cap}
        self.assertIs(cli._resolve(caps, "subtitles"), cap)
        self.assertIs(cli._resolve(caps, "subtitle"), cap)
        self.assertIsNone(cli._resolve(caps, "title"))

    def test_missing_required_positional_exits_2(self):
        cap = Capability(name="synthetic4", summary="合成能力，仅用于契约测试",
                         handler=lambda ad, **kw: {},
                         params=(param("target", str, required=True),))

        class _Fake:
            def __init__(self, *a, **kw):
                pass

            def call(self, name, **kwargs):
                raise AssertionError("缺必填参数时不该真的调用能力")

        err = io.StringIO()
        with mock.patch.object(cli, "_caps", return_value={"synthetic4": cap}), \
             mock.patch.object(cli, "BilibiliAdapter", _Fake), \
             redirect_stdout(io.StringIO()), redirect_stderr(err):
            rc = cli.main(["synthetic4"])
        self.assertEqual(rc, 2)
        self.assertIn("usage", err.getvalue())


if __name__ == "__main__":
    unittest.main()
