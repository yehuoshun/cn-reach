# -*- coding: utf-8 -*-
"""极简 protobuf 解码：只够解 B站的分段弹幕响应。

为什么手写：项目承诺零第三方依赖，不引 protobuf 运行时。
这里只实现 varint / length-delimited / fixed32 / fixed64 四种 wire type，
足够把「repeated 消息」拆成 {字段号: 值} 的扁平字典，字段语义由调用方按真数据核对后映射。
"""
from __future__ import annotations

from typing import Iterator


def read_varint(buf: bytes, i: int):
    """读一个 varint，返回 (值, 新下标)。"""
    shift = 0
    val = 0
    while True:
        if i >= len(buf):
            raise ValueError("varint 越界")
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        if not b & 0x80:
            return val, i
        shift += 7
        if shift > 63:
            raise ValueError("varint 过长")


def iter_fields(buf: bytes) -> Iterator[tuple[int, int, object]]:
    """逐字段 yield (字段号, wire type, 值)；值为 int 或 bytes。"""
    i = 0
    n = len(buf)
    while i < n:
        key, i = read_varint(buf, i)
        field_no, wire = key >> 3, key & 7
        if field_no == 0:
            raise ValueError("字段号为 0")
        if wire == 0:
            val, i = read_varint(buf, i)
            yield field_no, wire, val
        elif wire == 2:
            ln, i = read_varint(buf, i)
            if i + ln > n:
                raise ValueError("length-delimited 越界")
            yield field_no, wire, buf[i:i + ln]
            i += ln
        elif wire == 1:
            yield field_no, wire, buf[i:i + 8]
            i += 8
        elif wire == 5:
            yield field_no, wire, buf[i:i + 4]
            i += 4
        else:
            raise ValueError(f"不支持的 wire type：{wire}")


def parse_messages(payload: bytes, field_no: int = 1) -> list[dict]:
    """把「repeated 嵌套消息」解成 [{字段号: 值}] 列表。

    只做结构化拆解，不猜语义 —— 字段含义由调用方拿真响应核对后映射。
    """
    out = []
    for f, wire, val in iter_fields(payload):
        if f != field_no or wire != 2:
            continue
        msg = {}
        for sub_f, sub_wire, sub_val in iter_fields(val):
            if sub_wire == 2:
                try:
                    msg[sub_f] = sub_val.decode("utf-8")
                except UnicodeDecodeError:
                    msg[sub_f] = sub_val
            else:
                msg[sub_f] = sub_val
        out.append(msg)
    return out
