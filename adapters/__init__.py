"""cn-reach adapters —— 每个平台一个适配器，对外暴露统一的能力面。"""

from importlib import import_module

# 平台名 -> 模块路径（按需加载，避免没装的依赖炸整个包）
REGISTRY = {
    "bilibili": "adapters.bilibili.adapter:BilibiliAdapter",
}


def get_adapter(platform: str):
    """按名字取适配器实例。未知平台抛 KeyError 并列出可用平台。"""
    if platform not in REGISTRY:
        raise KeyError(f"未知平台 {platform!r}，可用：{', '.join(sorted(REGISTRY))}")
    mod_path, cls_name = REGISTRY[platform].split(":")
    return getattr(import_module(mod_path), cls_name)()


def list_adapters():
    return sorted(REGISTRY)