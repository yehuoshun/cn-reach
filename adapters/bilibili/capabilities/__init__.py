# -*- coding: utf-8 -*-
"""能力注册表：把各模块声明的 CAPABILITIES 汇总成一张表。

模块分工：
  content  —— 内容取数（搜索 / 详情 / 播放 / 字幕 / 评论 / 弹幕 / 推荐）
  site     —— 站点结构（排行 / 热门 / 分区 / UP主 / 番剧 / 专栏 / 直播 / 收藏夹 / 动态）
  account  —— 登录态（我的信息 / 历史 / 稍后再看 / 关注 / 收藏夹 / 追番…）
"""
from __future__ import annotations

from . import account, content, site
from .spec import Capability, Param, param

MODULES = (content, site, account)

CAPABILITIES = {}
for _mod in MODULES:
    for _cap in getattr(_mod, "CAPABILITIES", ()):
        if not isinstance(_cap, Capability):
            raise TypeError(f"{_mod.__name__}: CAPABILITIES 里必须是 Capability")
        if _cap.name in CAPABILITIES:
            raise RuntimeError(f"能力重名：{_cap.name}")
        CAPABILITIES[_cap.name] = _cap


def get(name):
    try:
        return CAPABILITIES[name]
    except KeyError:
        raise KeyError(
            f"未知能力：{name}（可选：{', '.join(sorted(CAPABILITIES))}）") from None


def catalog():
    """能力清单（不含任何端点细节），给 CLI / doctor / 文档用。"""
    return [
        {
            "name": c.name,
            "summary": c.summary,
            "auth": c.auth,
            "params": [
                {"name": p.name, "type": p.type.__name__, "required": p.required,
                 "default": p.default}
                for p in c.params
            ],
        }
        for c in CAPABILITIES.values()
    ]
