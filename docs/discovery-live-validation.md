# 工具发现 MVP：首次真实运行记录

核验日期：2026-11-02（UTC）。这是本项目原创工程记录，不发布RSS摘要、官网正文、原始快照、可信标注文件或运行日志。

## 结果

助手按仓库 `discover-tools` Skill 执行真实 helper：获取23条 AIHOT 产品文章线索，经可信父 Agent 核对官网并建立一个工具候选 **[Gamma](https://gamma.app/)**。Gamma 官网将其定位为视觉内容创作平台；[官方定价页](https://gamma.app/pricing)提供免费入口信息。以上为来源声明，不是本项目验证了产品功能、额度或实际免费使用。

候选 `tool_c349fe616c0a50d06288` 的状态为 `needs_verification`：3项带定位引用的事实、7项硬门槛全部unknown、score=null、合格shortlist=0。confidence=0.5643是固定公式的证据覆盖指标，不是产品质量/成功概率。厂商主页和定价页同属一个控制主体，不能算两份独立证据。

没有创建账号、登录、付款、生成演示作品、控制桌面、录屏或发布，也没有创建 avcd test 任务。一个“可继续实测的研究线索”与“已经完成实测/已获录制授权的工具”明确区分。

## 实际运行链

| run ID | UTC开始时间 | 结果 |
| --- | --- | --- |
| `aihot-live-20261102-01` | 14:21:21 | 默认direct本地DNS不可用，partial、0条线索；没有伪造成功 |
| `aihot-live-20261102-03` | 14:39:46 | 显式宿主managed_proxy，RSS HTTP200，23条线索 |
| `aihot-gamma-20261102-01` | 14:42:20 | 父Agent批准gamma.app；RSS304、官网200、定价页200 |
| `aihot-gamma-20261102-02` | 14:44:14 | 可信本地身份/事实标注；三个来源304复用缓存并保留独立检索记录；23条线索、1个工具、0个合格项 |

中间一次提升执行尝试在启动器阶段失败，未执行目标采集；没有把它计为成功请求。后续使用的是环境已经提供的受管网络通道，而非新建私人代理。配置、数据库、缓存及标注只在忽略的 `var/discovery/`。

## 快照指纹与来源

最终 run 的本次检索时间分别为14:44:20、14:44:24、14:44:26 UTC；304表示源内容未变，**不是新的内容发布日期**。

| 来源 | SHA-256 |
| --- | --- |
| [AIHOT产品RSS](https://aihot.news/feed/category/ai-products.xml) | `c7e7cfafd85eab99ac48e1cba6c4843169d565d2c0c46544df4c4c823a0b5452` |
| [Gamma官网](https://gamma.app/) | `a4e860cc271e8ef3d6ebf46522372b975f93034a0afdfbada506ffab33330c54` |
| [Gamma定价](https://gamma.app/pricing) | `8db9bbba1f62715a30f3df7ce8ec02cfed3a2d230105cc56663d2e3050ef13bc` |

这些指纹用于内部可复核性，不代表网站内容永远不变或赋予再分发权。没有在Git提交源正文。

## 测试与实施范围

- 35项离线单元测试通过：解析/缓存/去重/证据定位/未知门槛/UTC时间/HTML转义及direct与managed_proxy边界。网络测试使用模拟或本地对象，不伪称所有网络攻击场景已实网验证。
- 原有23项架构契约fixture、16项发现设计检查通过；它们验证设计样例，不代表完整34表系统已实现。
- `compileall`、`git diff --check`、Skill格式和本次新增文档本地链接检查通过。

现有实现是助手/researcher的Skill加确定性Python helper，不是持续自主运行的选择器或守护进程。默认direct会验证全部DNS结果并固定公网IP；真实环境使用的显式managed_proxy把目标DNS/出站策略交给宿主受管代理，仍执行获准域名、TLS、重定向与预算检查；**不能把direct本地DNS固定保证宣称为此模式的保证**。

robots/站点条款与刷新政策没有自动化执行；主体独立性、事实语义及门槛证据需要可信review。完整协调器、并发状态机、强身份自动提取、生产迁移/恢复与测试录屏Agent均不在此次交付范围。更多限制见[实施说明](discovery-mvp.md)。

本次等待时间包含一次旧工作流下Claude代码初稿的长时间推理（父Agent主动Ctrl-C，exit130，未产生代码），以及网络环境适配和安全测试。没有将主动终止写成CLI超时或声称Claude完成这次代码。现有设计稿出自先前Claude工作流；本次代码实施、review和测试由助手完成，遵循用户最新分工偏好。
