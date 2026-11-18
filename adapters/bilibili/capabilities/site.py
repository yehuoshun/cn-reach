# -*- coding: utf-8 -*-
"""L1 站点结构能力：排行榜 / 热门 / 分区 / UP主信息与投稿 / 番剧影视 / 专栏 / 直播 / 公开收藏夹 / 关注数 / 动态。"""
from __future__ import annotations

from .. import client
from .spec import Capability, param

# 由本模块填充；注册表见 capabilities/__init__.py
CAPABILITIES: list[Capability] = []
