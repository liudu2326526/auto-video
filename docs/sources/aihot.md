# AIHOT 信息源接入设计

日期：2026-11-02 · 状态：用户已确认站点，已加入设计；尚未接入生产、未运行发现、未创建定时任务。

## 定位与使用边界

用户确认的信息源为 [AIHOT](https://aihot.news/)，原域名 `aihot.virxact.com` 当前跳转到该站。它是 AI 动态聚合与摘要索引，提供产品、模型、教程、论文和行业消息。作为工具发现的 **P2 线索来源**，不能代替产品官网、官方文档或仓库的能力证据，也不能以热度推断适用性或效果。

本接入说明是主审根据用户新增需求及官方公开说明补充，不把它冒充为初稿中已经存在的功能。

## 已核查的官方接入说明

优先只接只读 RSS；需要字段/过滤能力时再评估 REST。不安装第三方 Skill，不必引入 MCP 或新账号。

| 路线 | 地址或约定 | 设计用途 |
| --- | --- | --- |
| 产品 RSS | `https://aihot.news/feed/category/ai-products.xml` | MVP 默认线索入口 |
| 教程 RSS | `https://aihot.news/feed/category/tip.xml` | 可选补充，可能是教程而不是新工具，必须分类 |
| REST | `https://aihot.news/api/v1/items` | 可选适配器；文档列出 category、mode、window 筛选 |
| 参数示例 | `category=ai-products&mode=selected&window=7d` | 人工触发 run 的样例参数，不代表当前已请求该查询 |
| 缓存 | ETag / 条件请求；RSS 建议刷新间隔 30 分钟 | 若未来启用调度，按最新官方要求及预算配置；本次不建立调度 |
| 旧接口 | `/api/public/*` 已停用 | 禁止新增依赖；失败不回退到旧接口 |

核查范围为官方公开页面与说明，不是完整接口、限流、数据质量和运行可用性验收。实施时再次验证 schema、分页、水位、请求上限与错误语义；不能猜测 cursor、固定配额或服务 SLA。

官方来源：[关于](https://aihot.news/about)、[Agent 接入](https://aihot.news/agent)、[RSS 说明](https://aihot.news/agent?tab=rss)、[API 说明](https://aihot.news/agent?tab=api)、[OpenAPI](https://aihot.news/openapi-v1.json)。

## 提议的来源配置（设计示例）

```json
{
  "source_id": "aihot_products",
  "source_name": "AIHOT 产品动态",
  "identity_confirmed": true,
  "integration_state": "planned",
  "enabled": false,
  "priority": 2,
  "adapter": "rss",
  "url": "https://aihot.news/feed/category/ai-products.xml",
  "allowed_domains": ["aihot.news"],
  "content_role": "discovery_lead",
  "requires_primary_source_verification": true,
  "access_policy_status": "pending_use_case_review",
  "cache_policy": {"conditional_get": true, "minimum_refresh_seconds": 1800},
  "schedule": null
}
```

`enabled=false` 表示没有已部署采集器，不表示站点身份仍未确认。实施时先核实本项目的具体使用方式及许可，再启用；不把源配置写进运行时或启动定时器。

## 字段映射与处理

1. 保存 source_id、来源条目 ID（若提供）、AIHOT 条目链接、原文链接、标题、发布时间、抓取时间、分类、原始内容哈希及本次获取记录。
2. 原文链接、摘要中的产品 URL 和名称只作为待核查线索。一个条目可提及多个产品；多条动态也可属于同一产品。条目 ID 不能当作 tool_id。
3. 标题、摘要、榜单位置属于第三方整理；引用必须标注 AIHOT 与原文来源，禁止改写成亲测结论。
4. 可进入评分的能力、价格、免费额度、访问要求须回到当前官网/官方仓库核验；来源间转载不算独立佐证。
5. 原文域名不在白名单时输出 `proposed_source_domains`，由受信任的来源准入流程批准后才能访问。网页、RSS 或重定向不能自动扩大网络权限。
6. 条目去重基于稳定条目键；工具去重使用专项方案的强身份与实体规则。每次获取记录独立保留，正文 blob 可按哈希去重。
7. RSS 与 API 作为同一来源的不同通道，不能重复计入来源独立性。条件请求返回 304 时复用已核验内容并更新获取记录，不虚构新的内容发布时间。

## 许可门槛

官方说明区分内部使用与对外商业产品、收费服务、代理或批量再分发。匿名可访问不等于所有用途获准，也不等于可将摘要、配图或第三方文章直接用于公开视频。

- 当前只设计用于发现线索并回到原站核实，不授予抓取或再分发权限。
- auto-video 若用于商业内容生产，需按实际用法确认适用条款；对外产品、收费或批量再分发按官方要求取得授权。
- 保存条款版本与核查日期。条款用途不清、限制冲突或需授权时，保持来源未启用并请求人工确认。
- 视频素材仍走独立的素材许可门槛，不能继承“来源 API 可读”的权限。

## 实施验收清单（未执行）

- RSS/API 响应类型与字段校验，网络失败/429/304 的正确处理。
- 同一条目跨 RSS/API 去重，不合并不同产品，不把一篇文章当成一个工具。
- 产品/论文/教程/行业消息分类准确，非工具条目不得为凑数入选。
- AIHOT、原文与官网证据均可追溯；过期价格与未证实功能不会变成已核实事实。
- 外链、重定向和 RSS 内嵌恶意指令不能扩张白名单或执行代码。
- 无账号、密钥、定时器或内容再分发被自动创建。
