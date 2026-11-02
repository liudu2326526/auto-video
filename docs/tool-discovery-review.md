# 工具发现 Agent 专项 Review

日期：2026-11-02 · 范围：设计审查、SQL/JSON 示例及来源说明，不是发现系统实现。

## 生成与新增需求

`tool-discovery-agent.md` 初稿由实际 Claude Code 2.1.316 生成，配置模型 Opus，CLI 正常退出（exit 0 / success / 无权限拒绝），随后经独立审查及主审修订。保留 Codex Agent Team 的 researcher 岗位，不新增第六个常驻 Agent。

用户随后确认 AI Hot 指的是 [AIHOT](https://aihot.news/)。主审根据官方公开说明补充 [AIHOT 接入设计](sources/aihot.md)，加入产品 RSS 与可选 v1 API 线索路线。没有安装 Skill/MCP、运行来源采集或设置定时任务。具体用途许可与生产接入仍需验收。

## 关键发现与处理

| 等级 | 问题 | 修订 |
| --- | --- | --- |
| P1 | 示例在地区可达性、条款未知时仍 shortlisted 且 ready=true | 七项门槛逐项 pass/fail/unknown；未知不评分、不入选、不交接；示例保持 needs_verification |
| P1 | 全局候选表存本次 run 的 status/rank/score，覆盖历史 | 工具总表只管身份；评估归属 run/item，历史短名单 payload 不可覆盖 |
| P1 | 快照按内容哈希唯一，丢失多源与刷新证据；事实同日唯一压掉矛盾 | 内容 blob 去重与每次检索身份分离，同日多源事实均保留 |
| P1 | 原 selected 被回填 handed_off，虚构任务已送达 | 保留旧 decision；只有幂等创建真实 test task 并保存 task_id 后才能交接完成 |
| P1 | 状态枚举不包含文字流程中的 needs_input/truncated/budget_blocked | 补 source 状态；运行预算阻塞用 needs_input + error_class 明确表达 |
| P1 | ALTER 示例不可重复执行，却承诺迁移幂等 | 版本化迁移事务只执行一次，重复请求先查版本；版本执行器仍未实现 |
| P2 | confidence 未定义分子分母，引用未知项被排除抬高指标 | 定义可复算公式、独立控制主体、冻结分母；不可核实引用仍计分母 |
| P2 | 去重召回误叫准确率，短名单不足数混入精确率 | 分开报告精确率、覆盖率、去重召回与误合并；空分母报告 N/A |
| P2 | 来源失败会错误停止整次 run，HTTPS 降级规则含糊 | 单源/单候选局部停止；HTTPS 默认、重定向逐跳校验、硬边界不可由页面扩张 |
| P2 | 同一文章被当成工具身份，跨域转跳会误合并 | 摄取条目与工具实体拆开，多产品 occurrence；规范化后每工具每 run 一项 |
| P2 | 新增 AIHOT 可读被误认为可直接商用内容 | 仅作线索、回官网核实，许可与素材复用分开；不自动启用或再分发 |

## 已执行的验证

- `python3 scripts/check_discovery_design.py`：16 项文档检查通过，2 个发现阶段 SQL 块和 2 个 JSON 示例通过。
- `python3 scripts/check_design_contracts.py`：原方案 23 项契约 fixtures、9 个 DDL 块和 3 个 JSON 示例通过，未破坏已有设计示例。
- AIHOT 来源配置 JSON 可解析；相对文档链接目标存在；Python 语法与 `git diff --check` 通过。
- 待提交文件无密钥模式命中或本机绝对路径（启发式扫描，不等于穷尽安全审计）。

检查覆盖：旧 decision 保持、多 run 独立评分、多次同内容检索、同日冲突事实、source 枚举、无 task_id 交接拒绝、分数越界拒绝、外键一致性、七项门槛、示例未知状态、confidence 重算和哈希格式。

## 不属于本次通过范围

没有运行真实发现、训练或质量评测集，没有真实候选被实测。16 项检查不是 D-T1–D-T21 的运行时全部验收。尚未实现版本迁移执行器、网络白名单/SSRF 代理、来源适配器、收费调用预算强制、run 状态机与 test 交接服务。样例阈值、候选数量和预算都是可调整设计假设。

建议先实现手动触发、1–2 个获准来源、保留证据的窄链路；不要先做定时全网爬取或承诺推荐效果。
