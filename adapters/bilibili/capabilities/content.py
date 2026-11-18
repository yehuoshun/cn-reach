# -*- coding: utf-8 -*-
"""L1 内容取数能力：搜索 / 视频详情 / 分P / 播放地址 / 字幕 / 评论 / 子评论 / 弹幕 / 相关推荐 / 在线人数。"""
from __future__ import annotations

from .. import client
from .spec import Capability, param

# 由本模块填充；注册表见 capabilities/__init__.py
CAPABILITIES: list[Capability] = []
