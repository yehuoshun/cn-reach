# cn-reach 🇨🇳

> **让 AI Agent 真正用上中国平台。**
> B站、小黑盒、微博、知乎、贴吧、抖音、小红书…… 一个统一的多后端接入层。

`cn-reach` 是给 AI Agent（OpenClaw / Claude / Cursor / 任意 MCP 客户端）用的「中国平台能力路由器」：
把每个平台的**只读能力**（搜索、榜单、详情、字幕、评论）和未来的**受控写操作**收敛成一套一致的调用方式，
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

| 平台 | 能力 | 后端设想 | 状态 |
|---|---|---|---|
| **B站 / Bilibili** | 搜索、视频详情、字幕、评论、UP 主页 | bili-cli / 官方 web API + Cookie | 🧪 预研 |
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

## 设计取向

1. **多后端路由，不重造轮子。** 每个平台是一个 adapter，背后可挂 API / CLI / MCP / 浏览器登录态；上层统一命令。
2. **失败可降级。** 一种后端不通（限流、风控、Cookie 失效），自动落到下一顺位。
3. **凭据显式。** Cookie / Token 一律走环境变量或独立凭据文件，**绝不进仓库**。
4. **只读默认，写操作显式。** 任何会改变平台状态的动作必须单独开启。
5. **可诊断。** 一条命令体检所有平台当前可用的后端（类似 `agent-reach doctor`）。

---

## 目录结构（规划）

```
cn-reach/
├── adapters/           # 每个平台一个适配器
│   └── bilibili/
├── router/             # 后端选择 + 降级逻辑
├── docs/
│   └── adding-a-platform.md
└── README.md
```

---

## 快速开始

> 🚧 项目刚起步，接口尚未稳定。想跟进或参与，先 Star + Watch。

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
