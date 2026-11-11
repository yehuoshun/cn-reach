# cn-reach 🇨🇳

> **让 AI Agent 真正用上中国平台。**
> B站、小黑盒、微博、知乎、贴吧、抖音、小红书…… 一个统一的多后端接入层。

`cn-reach` 是给 AI Agent（OpenClaw / Claude / Cursor / 任意 MCP 客户端）用的「中国平台能力路由器」：
把每个平台的**只读能力**（搜索、详情、榜单、字幕、评论）收敛成一套一致的调用方式，
Agent 不用关心背后是官方 API、第三方 CLI、浏览器登录态还是 MCP server。

> 🌍 兄弟项目：海外平台交给 [Agent-Reach](https://github.com/Panniantong/Agent-Reach)；
> `cn-reach` 专注**国内平台**——尤其那些没有公开 API、或 API 门槛高的服务。

---

## 为什么做这个

- 海外有 Composio / Nango 这类「一个平台连上千个 App」的聚合层，**国内没有对应的开源实现**。
- 国内平台各自为政：官方 API 要么没有、要么要企业资质；能用的多是社区 CLI / 浏览器自动化，散落各处。
- Agent 想要「看一眼小黑盒上这个游戏的风评」「翻一下 B站这条视频的评论区」，现在得逐个人肉拼装。

`cn-reach` 想做的事很简单：**给 Agent 一双手，能伸进国内平台拿信息。**

---

## 平台矩阵

状态口径：`✅ 可用` = 已能稳定调用 · `🧪 预研` = 有可行路径待验证 · `🗺️ 规划` = 只有接口设想 · `🙋 招募` = 欢迎 PR

| 平台 | 能力 | 后端 | 状态 |
|---|---|---|---|
| **B站 / Bilibili** | 搜索、视频详情、字幕、评论 | 官方 web API（wbi 签名 + Cookie） | ✅ 可用 |
| **小黑盒 / HeyBox** | 游戏详情、评分、资讯、社区帖子 | 登录态 API（逆向） | 🗺️ 规划 |
| 微博 / Weibo | 搜索、热搜、用户时间线 | web API + Cookie | 🗺️ 规划 |
| 知乎 / Zhihu | 搜索、问答、专栏 | web API + Cookie | 🗺️ 规划 |
| 百度贴吧 / Tieba | 吧内帖子、搜索 | web API | 🗺️ 规划 |
| 抖音 / Douyin | 用户、视频、搜索 | 登录态 / 第三方 | 🗺️ 规划 |
| 豆瓣 / Douban | 条目、评分、短评 | 公开 API | 🗺️ 规划 |
| 小红书 / Xiaohongshu | 搜索、笔记 | 登录态 MCP | 🤝 见 awesome-china-mcp |
| NGA / 虎扑 | 板块、帖子 | web API | 🙋 招募 |

> 只读优先。写操作（发帖、评论、点赞）默认不提供；确需时走显式、可审计的开关。

---

## 快速开始

零第三方依赖（纯 Python 标准库，Python ≥ 3.10）。B站需要登录态才能拿字幕：

```bash
# 1) 放好 cookie（含 SESSDATA）。默认路径：
#    ~/.openclaw/secrets/bilibili-cookies.txt
#    也可用环境变量 CNREACH_BILI_COOKIE 指定

# 2) 体检：cookie / 登录态 / wbi 是否就绪
export PYTHONPATH=.
python3 -m adapters.bilibili.cli doctor

# 3) 搜索
python3 -m adapters.bilibili.cli search "崩坏3 爱莉希雅" --limit 5 --order pubdate

# 4) 视频详情
python3 -m adapters.bilibili.cli video BV1tXx56WECd

# 5) 字幕（无字幕时自动兜底「简介 + 热评」，并明确标记不是字幕）
python3 -m adapters.bilibili.cli subtitle BV1bU4y1s7Cr --max-chars 3000

# 6) 评论
python3 -m adapters.bilibili.cli comments BV1tXx56WECd --limit 20 --sort hot
```

所有子命令输出 JSON；错误走 stderr 并以非零码退出，错误分类为
`api / risk / network / auth / not_found`，方便上层做降级。

也可当库调用：

```python
from adapters import get_adapter

bili = get_adapter("bilibili")          # 目前唯一注册的适配器
print(bili.search("原神", limit=5)["items"][0])
```

---

## 测试

零依赖（标准库 `unittest`），CI 在 push / PR 时跑 Python 3.10 / 3.12。

```bash
# 离线单元测试（mock 掉网络层）—— 24 条
python -m unittest discover -s tests -t . -v

# 联网冒烟测试（默认跳过，需本机有效 cookie）—— 5 条
CNREACH_LIVE=1 python -m unittest tests.test_bilibili_live -v
```

测试覆盖：BV 解析、**wbi 签名（含官方文档已知向量）**、请求层错误归一化
（`-412` 风控立即失败 / 业务错误不重试）、搜索与详情归一化、字幕可用性与**语速可信度**
（含多P 按分P 时长）、无字幕兜底、评论归一化、适配器注册表。

> CI 只跑离线用例；联网用例依赖真实 IP 与 cookie，不进 CI（B站出口 IP 容易触发 `-412`）。

---

## 设计取向

1. **多后端路由，不重造轮子。** 每个平台是一个 adapter，背后可挂 API / CLI / MCP / 浏览器登录态；上层统一命令。
2. **失败可降级。** 一种后端不通（限流、风控、Cookie 失效），自动落到下一顺位。
3. **凭据显式。** Cookie / Token 一律走环境变量或独立凭据文件，**绝不进仓库**。
4. **只读默认，写操作显式。** 任何会改变平台状态的动作必须单独开启。
5. **结果可信度要标注。** 拿不准的数据（如字幕疑似错配）不静默返回，而是显式打上 `suspect` 标记。
6. **可诊断。** 一条命令体检所有平台当前可用的后端（`doctor`）。

---

## 目录结构

```
cn-reach/
├── adapters/
│   ├── __init__.py            # 适配器注册表 + get_adapter()
│   ├── bilibili/              # ✅ B站（只读）
│   │   ├── client.py          # 请求层：cookie / 重试退避 / wbi 签名 / 错误归一化
│   │   ├── adapter.py         # 能力层：search / video / subtitles / comments
│   │   └── cli.py             # 命令行：python -m adapters.bilibili.cli
│   └── <platform>/            # 其它平台照此新增
├── tests/
│   ├── test_bilibili_unit.py  # 离线单元测试（mock 网络）
│   └── test_bilibili_live.py  # 联网冒烟（默认跳过）
├── docs/
│   └── adding-a-platform.md   # 新增平台适配器的规范
└── README.md
```

---

## 参考项目与致谢

`cn-reach` 站在这些项目的肩膀上。以下为**思路与代码来源**，均为 MIT 许可：

| 项目 | 借鉴了什么 |
|---|---|
| [yehuoshun/bili-digest](https://github.com/yehuoshun/bili-digest) | 适配器的原点：BV 解析、wbi 签名取字幕、**无字幕时用「简介 + 热评」兜底**、中文（含 `ai-zh`）字幕优先的选取策略 |
| [yehuoshun/interest-radar](https://github.com/yehuoshun/interest-radar) | 请求层范式：**指数退避 / 429 尊重 Retry-After / -412 与 `v_voucher` 风控熔断**；wbi 搜索接口（`wbi/search/type`）；字幕**可信度校验**（按语速 3~15 字/秒判定，两次结果不一致即判不可信） |
| [Panniantong/Agent-Reach](https://github.com/Panniantong/Agent-Reach) | 整体设计口径：多后端路由、`doctor` 体检、平台矩阵命名（项目名与之成对） |
| [zackchewa/awesome-china-mcp](https://github.com/zackchewa/awesome-china-mcp) | 国内 MCP 生态索引，平台覆盖面的对标参照 |

> 一个踩过的坑（来自 interest-radar 的实测）：旧接口 `x/player/v2` **不带 wbi 签名会返回别的视频的字幕**。
> `cn-reach` 只用签名接口 `x/player/wbi/v2`，并保留「语速合理性」二次校验。

---

## 参与贡献

- 缺一个平台适配器？见 [`docs/adding-a-platform.md`](docs/adding-a-platform.md)。
- 发现某平台有更好的现成后端（CLI / MCP / API）？开 issue 贴上链接。
- **禁止**提交任何 Cookie、Token、账号或抓取到的用户隐私数据。

---

## 相关项目

- [Agent-Reach](https://github.com/Panniantong/Agent-Reach) — 海外平台能力路由器
- [Awesome China MCP](https://github.com/zackchewa/awesome-china-mcp) — 国内 MCP Server 索引
- [bili-digest](https://github.com/yehuoshun/bili-digest) — B站视频摘读
- [interest-radar](https://github.com/yehuoshun/interest-radar) — 每日 B站兴趣雷达

---

## License

[MIT](LICENSE)
