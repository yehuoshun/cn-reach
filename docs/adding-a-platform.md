# 如何新增一个平台适配器

每个平台在 `adapters/<platform>/` 下自成一体，对外只暴露一组**稳定、最小**的能力。

> 📖 参考实现：[`adapters/bilibili/`](../adapters/bilibili/)（B站，只读）——它按本文的结构拆成
> `client.py`（请求层）/ `adapter.py`（适配层）/ `capabilities/`（**能力注册表**）/ `cli.py`（命令行），
> 新增平台可照此复制。

## 0. 先想清楚「对外暴露什么」

本项目对外只暴露**能力（capability）**，不暴露**端点（endpoint）**。
能力是给 Agent 看的一句话契约（`search`、`ranking`、`my_folders`），
端点路径、签名流程、请求头这些实现细节只留在代码里，不进公开文档。

原因很实际：把某个平台的接口全集逐条整理成公开文档，在部分平台是会被发律师函的行为
（社区里已经有过先例）。所以本项目的口径是——**用能力描述「能做什么」，不用端点描述「怎么做到的」**。

## 1. 定能力面

只读优先。一个适配器通常实现：

| 能力 | 说明 |
|---|---|
| `search` | 关键词搜索 |
| `detail` | 按 id 取详情 |
| `list` | 榜单 / 时间线 / 板块 |
| `comments` | 评论（如平台支持） |

写操作（发帖 / 评论 / 点赞）**默认不实现**；确需时单独走显式开关，并写清风险。

能力名一旦发布就是**稳定契约**，不要随手改名；新增能力只会让表变长，不影响已有调用方。
命名口径：动词开头、小写、能一眼看出拿的是什么（`video` / `up_videos` / `my_folders`），
不要带平台前缀（平台已经在适配器层了）。

## 1.5 用能力注册表声明能力

每个平台在 `adapters/<platform>/capabilities/` 下按主题分模块（如 B站拆成
`content` / `site` / `account` 三个模块），每个模块只做一件事：往 `CAPABILITIES` 列表里登记能力。

```python
from .. import client                     # 请求层：模块导入，方便测试 patch
from .spec import Capability, param

def _xxx(ad, keyword, limit=20):
    cookie = ad.cookie                    # 由适配器注入，别在 handler 里自己 load
    data = client.get_json(f"{client.API}/...", cookie)["data"]
    return {"platform": "bilibili", ...}  # 返回体必须带 platform

CAPABILITIES = [
    Capability(
        name="search",
        summary="关键词搜索视频",
        handler=_xxx,
        params=[param("keyword", required=True, help="搜索词"),
                param("limit", int, default=20, help="返回条数")],
        auth=False,                       # True = 需要登录态，没 cookie 时直接报 auth 错
    ),
]
```

注册表（`capabilities/__init__.py`）会自动汇总所有模块，并检查重名；
CLI 子命令与 `catalog()` 清单都由它生成，不需要再手写一遍。

硬规矩：

- 返回体统一 dict，**必须**含 `"platform"`；列表字段用复数（`items` / `comments` / `pages`）。
- 失败抛 `BiliError(kind=...)`（`api / risk / network / auth / not_found`），不要返回空壳假装成功。
- 时间字段统一按**北京时间**渲染成字符串，别依赖本机时区。
- `auth=True` 的能力在无 cookie 时必须**不发请求**就报错。
- 零第三方依赖：能用手写实现的（比如 protobuf）就不要引库。

## 2. 选后端

按优先级挑，**不重造轮子**：

1. 官方 API（有就用，最稳）
2. 现成社区 CLI / MCP server（先复用，别自己写）
3. 登录态 web API（Cookie / Token 显式注入）
4. 浏览器自动化（最后手段，成本最高）

一个平台可以同时登记多个后端，交给 router 做降级。

## 3. 凭据

- 一律从环境变量或独立凭据文件读取。
- **绝不**把 Cookie / Token / 账号写进代码、示例或提交记录。
- 凭据失效要能明确报错，而不是静默返回空结果。
- 路径用 `pathlib.Path.home()` 拼，**不要硬编码** `/` 或 `\`；默认路径要能用环境变量覆盖。

## 4. 体检

适配器要能被统一体检命令探测：输出「当前激活后端 / 是否登录 / 是否可用」，供 `doctor` 汇总。

## 5. 注册适配器

在 `adapters/__init__.py` 的 `REGISTRY` 里加一行 `"<platform>": "adapters.<platform>.adapter:<Platform>Adapter"`，
让统一入口 `get_adapter("<platform>")` 认得它。

## 6. 提交前自查

```bash
git diff --cached | grep -iE '(token|secret|cookie|password|api_key|webhook)' | grep -v '.example'
```

有命中先问维护者，别自作主张删。
