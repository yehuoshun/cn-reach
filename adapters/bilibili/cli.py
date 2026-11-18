"""B站适配器 CLI —— 注册表驱动。

    python -m adapters.bilibili.cli list                    # 列出全部能力
    python -m adapters.bilibili.cli doctor                  # cookie / 登录态 / wbi 体检
    python -m adapters.bilibili.cli <能力名> [位置参数] [--参数 值]

子命令由能力注册表（capabilities.CAPABILITIES）自动生成，新增能力不用改这里。
参数约定：必填参数当位置参数传（保持 README 里的旧写法可用），
可选参数用 --名字 值；下划线自动转横线（page_size → --page-size）。
必填参数也接受 --名字 形式。

    python -m adapters.bilibili.cli search "关键词" --limit 5 --order pubdate
    python -m adapters.bilibili.cli video BV1tXx56WECd
    python -m adapters.bilibili.cli subtitle BV1bU4y1s7Cr --max-chars 3000
    python -m adapters.bilibili.cli comments BV1tXx56WECd --limit 20 --sort hot

输出 JSON（stdout）。错误走 stderr：BiliError → 退出码 2；doctor 体检不通过 → 退出码 3。
"""
from __future__ import annotations

import argparse
import json
import sys

from .adapter import BilibiliAdapter
from .client import BiliError

# 保留子命令，优先于能力名解析
RESERVED = ("list", "doctor")


def _force_utf8() -> None:
    """Windows 上 stdout 重定向到管道/文件时默认走本地编码（如 cp936），中文会炸；
    统一改 UTF-8（Python 3.7+）。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8")
            except Exception:  # noqa: BLE001 —— 重定向场景下失败就保持原样
                pass


def _dump(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _caps() -> dict:
    """能力表 {名字: Capability}。延迟导入，避免仅看 help 就拉起整条链路。"""
    from .capabilities import CAPABILITIES
    return CAPABILITIES


def _resolve(caps: dict, name: str):
    """按名字找能力。查不到时允许「单复数 / 前后缀」唯一匹配（subtitle → subtitles），
    保证 README 里更早的写法不用改；命中多个则算未知，老老实实报错。"""
    if name in caps:
        return caps[name]
    hits = [cap for cname, cap in caps.items()
            if cname.startswith(name) or name.startswith(cname)]
    return hits[0] if len(hits) == 1 else None


def _flag(prm) -> str:
    return "--" + prm.name.replace("_", "-")


def _cap_parser(cap) -> argparse.ArgumentParser:
    """按能力的参数声明生成 argparse。"""
    p = argparse.ArgumentParser(prog=f"cn-reach bilibili {cap.name}", description=cap.summary)
    # 必填参数 → 位置参数（旧写法：search "关键词" / video BV号）
    for prm in cap.params:
        if prm.required:
            p.add_argument(f"_pos_{prm.name}", metavar=prm.name, nargs="?",
                           default=None, type=prm.type,
                           help=f"{prm.help or prm.name}（必填）")
    # 所有参数都再给一份 --名字 形式
    for prm in cap.params:
        if prm.type is bool:
            p.add_argument(_flag(prm), dest=prm.name, action="store_true",
                           default=None, help=prm.help or prm.name)
        else:
            p.add_argument(_flag(prm), dest=prm.name, metavar=prm.name.upper(),
                           type=prm.type, default=None, help=prm.help or prm.name)
    return p


def _collect(cap, ns) -> dict:
    """把 argparse 结果收成 handler 的 kwargs；未传的不塞，让 bind() 走默认值校验。"""
    out = {}
    for prm in cap.params:
        val = getattr(ns, prm.name, None)
        if val is None and prm.required:
            val = getattr(ns, f"_pos_{prm.name}", None)
        if val is not None:
            out[prm.name] = val
    return out


def _missing_required(cap, kwargs: dict) -> list:
    """必填参数是否漏传 —— 在构造 adapter、发请求之前就拦下来。"""
    return [p.name for p in cap.params if p.required and p.name not in kwargs]


def _print_help(caps: dict) -> None:
    print("用法: python -m adapters.bilibili.cli <子命令> [参数]")
    print()
    print("子命令:")
    print("  list      列出全部能力（名字 / 说明 / 是否需要登录 / 参数）")
    print("  doctor    检查 cookie / 登录态 / wbi（体检不通过退出码为 3）")
    print(f"  <能力名>  由注册表自动生成，当前可用: {', '.join(sorted(caps))}")
    print()
    print("必填参数当位置参数传，可选参数用 --名字 值（page_size 写作 --page-size）。")
    print('例: python -m adapters.bilibili.cli search "关键词" --limit 5 --order pubdate')


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    argv = list(sys.argv[1:] if argv is None else argv)
    caps = _caps()

    if not argv or argv[0] in ("-h", "--help", "help"):
        _print_help(caps)
        return 0

    cmd, rest = argv[0], argv[1:]

    if cmd == "list":
        from .capabilities import catalog
        _dump(catalog())
        return 0

    if cmd == "doctor":
        out = BilibiliAdapter().doctor()
        _dump(out)
        # 体检不通过必须是非零退出码（旧版错返回 0，运维脚本会把亚健康当成健康）
        return 0 if out.get("ok") else 3

    cap = _resolve(caps, cmd)
    if cap is None:
        print(f"未知子命令：{cmd}", file=sys.stderr)
        print(f"可用子命令：{', '.join(RESERVED + tuple(sorted(caps)))}", file=sys.stderr)
        return 2

    ns = _cap_parser(cap).parse_args(rest)
    kwargs = _collect(cap, ns)
    missing = _missing_required(cap, kwargs)
    if missing:
        print(f"[usage] 缺少必填参数：{', '.join(missing)}", file=sys.stderr)
        _cap_parser(cap).print_usage(sys.stderr)
        return 2
    try:
        out = BilibiliAdapter().call(cap.name, **kwargs)
    except BiliError as e:
        print(f"[{e.kind}] {e}", file=sys.stderr)
        return 2
    except ValueError as e:
        # bind() 校验失败：缺必填参数 / 未知参数
        print(f"[usage] {e}", file=sys.stderr)
        return 2
    _dump(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
