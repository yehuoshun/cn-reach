# -*- coding: utf-8 -*-
"""能力（capability）注册契约。

设计口径：对外只暴露「能力」，不暴露「端点」。
每个能力 = 名字 + 一句话说明 + 参数表 + 是否需要登录态 + 处理函数；
端点路径、签名流程、请求头等实现细节只留在代码里，不进公开文档。

新增能力：在自己的模块里往 CAPABILITIES 列表里加一个 Capability 即可，
注册表（capabilities/__init__.py）会自动汇总，CLI 子命令也随之生成。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Param:
    """能力参数声明：CLI 按它生成 argparse，bind() 按它做校验。"""

    name: str
    type: type = str
    required: bool = False
    default: Any = None
    help: str = ""


def param(name: str, type: type = str, required: bool = False,
          default: Any = None, help: str = "") -> Param:
    return Param(name=name, type=type, required=required, default=default, help=help)


@dataclass(frozen=True)
class Capability:
    """一个只读能力。

    handler(adapter, kwargs) -> dict，返回体必须带 "platform": "bilibili"。
    """

    name: str
    summary: str
    handler: Callable[..., dict]
    params: tuple = ()
    auth: bool = False        # True = 需要登录态（账号相关能力）
    mutating: bool = False    # 只读项目：永远是 False，留作显式防线

    def bind(self, **kwargs) -> dict:
        """只保留声明过的参数，缺省的用默认值；必填缺失 / 未知参数直接报错。"""
        known = {p.name for p in self.params}
        unknown = set(kwargs) - known
        if unknown:
            raise ValueError(f"{self.name}: 未知参数 {sorted(unknown)}")
        out = {}
        for p in self.params:
            val = kwargs.get(p.name)
            if val is not None:
                out[p.name] = val
            elif p.required:
                raise ValueError(f"{self.name}: 缺少必填参数 {p.name}")
            elif p.default is not None:
                out[p.name] = p.default
        return out

    def run(self, adapter, **kwargs) -> dict:
        if self.mutating:
            raise RuntimeError("只读项目不提供写能力")
        return self.handler(adapter, **self.bind(**kwargs))
