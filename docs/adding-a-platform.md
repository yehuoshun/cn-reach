# 如何新增一个平台适配器

每个平台在 `adapters/<platform>/` 下自成一体，对外只暴露一组**稳定、最小**的能力。

> 📖 参考实现：[`adapters/bilibili/`](../adapters/bilibili/)（B站，只读）——它按本文的结构拆成
> `client.py`（请求层）/ `adapter.py`（能力层）/ `cli.py`（命令行），新增平台可照此复制。

## 1. 定能力面

只读优先。一个适配器通常实现：

| 能力 | 说明 |
|---|---|
| `search` | 关键词搜索 |
| `detail` | 按 id 取详情 |
| `list` | 榜单 / 时间线 / 板块 |
| `comments` | 评论（如平台支持） |

写操作（发帖 / 评论 / 点赞）**默认不实现**；确需时单独走显式开关，并写清风险。

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
