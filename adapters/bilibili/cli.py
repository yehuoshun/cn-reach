"""B站适配器 CLI。

    python -m adapters.bilibili.cli doctor
    python -m adapters.bilibili.cli search "关键词" --limit 10 --order pubdate
    python -m adapters.bilibili.cli video BV1xx411c7mD
    python -m adapters.bilibili.cli subtitle BV1xx411c7mD --max-chars 3000
    python -m adapters.bilibili.cli comments BV1xx411c7mD --limit 20

所有子命令输出 JSON（stdout），错误信息走 stderr 并以非零码退出。
"""
from __future__ import annotations

import argparse
import json
import sys

from .adapter import BilibiliAdapter
from .client import BiliError


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
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(prog="cn-reach bilibili", description="B站只读能力")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="检查 cookie / 登录态 / wbi")

    p = sub.add_parser("search", help="搜索视频")
    p.add_argument("keyword")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=20)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--order", default="totalrank",
                   choices=["totalrank", "click", "pubdate", "dm"])

    p = sub.add_parser("video", help="视频详情")
    p.add_argument("target", help="BV 号或 URL")

    p = sub.add_parser("subtitle", help="字幕（无字幕时兜底简介+热评）")
    p.add_argument("target", help="BV 号或 URL")
    p.add_argument("--cid", type=int, default=None, help="分P cid，默认第一P")
    p.add_argument("--max-chars", type=int, default=None, help="截断正文字符数")

    p = sub.add_parser("comments", help="评论")
    p.add_argument("target", help="BV 号或 URL")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--sort", default="hot", choices=["hot", "time"])

    args = ap.parse_args(argv)
    ad = BilibiliAdapter()

    try:
        if args.cmd == "doctor":
            _dump(ad.doctor())
        elif args.cmd == "search":
            _dump(ad.search(args.keyword, page=args.page, page_size=args.page_size,
                            order=args.order, limit=args.limit))
        elif args.cmd == "video":
            _dump(ad.video(args.target))
        elif args.cmd == "subtitle":
            _dump(ad.subtitles(args.target, cid=args.cid, max_chars=args.max_chars))
        elif args.cmd == "comments":
            _dump(ad.comments(args.target, limit=args.limit, sort=args.sort))
    except BiliError as e:
        print(f"[{e.kind}] {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())