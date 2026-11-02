# Auto Video 技术设计

版本：v0.2（Claude Code 初稿，经独立 review 修订）· 日期：2026-11-02 · 分支：`work/auto-video-technical-design`

本文档描述系统的技术选择与边界，供工程团队实施。**它不是实现说明，也不表示任何组件已建成或已验收。**
配套文档：[数据与状态](data-and-state-contracts.md)、[实施计划](implementation-plan.md)、[验收计划](validation-plan.md)、[review 记录](review-report.md)。下文 doc2、doc3、doc4 分别指前三份文档。

## 0. 阅读约定：证据等级

全文用四个标记区分陈述来源，实施与评审时不得混用：

| 标记 | 含义 | 处理要求 |
| --- | --- | --- |
| `[已知]` | README 已写明的目标、约束或已核查结论 | 直接作为约束 |
| `[决定]` | 本文作出的工程设计选择 | 变更需改本文并留 revision |
| `[假设]` | 无外部核验、按经验设定的前提 | 实施前或首阶段必须证伪/证实 |
| `[待验]` | 需要真实账号、真实软件或实测才能确认的能力 | 不得在验收前对外声称可用 |

**运行时归属** `[已知]`：生产运行时是 Codex Agent Team（主聊天统筹 + 五个子 Agent）。本文所有"Agent"均指 Codex 运行时中的会话，不引入其他助手作为执行主体。本文作者使用何种编辑工具与生产运行时无关。

## 1. 目标与非目标

### 1.1 目标（首版可交付的最小闭环）

`[已知]` 从"选题"到"发布对账"走通一次，且每一步的完成以**落盘产物 + 校验通过 + 数据库提交**判定，而不是以 Agent 自述判定。

1. 一个网页工具的候选发现、来源记录与可复现实测场景。
2. 一次真实软件实测 + 正式录屏，产出可回溯到操作时刻的证据。
3. 基于实测事实冻结文案，生成一条约 60 秒 1080×1920/30fps 竖屏视频。
4. 全片音画质检通过，并保留质检依据。
5. 通过**人工明确批准**的交接通道完成一次发布，并核实外部最终状态。

### 1.2 非目标（首版明确不做）

| 不做 | 原因 | 何时可重议 |
| --- | --- | --- |
| 无人值守自动发布到抖音/小红书 | `[已知]` 真实账号与接口未验收；平台侧审批与风控不可控 | 阶段三验收后，且按平台能力单独准入 |
| GUI 剪辑软件（剪映/CapCut）自动导出 | `[待验]` README 记录公开 API 不足 | 适配器通过单条视频全链验证后 |
| 多桌面 / 多机并行 | `[决定]` 单机即可满足 60 秒短视频产能 | 见 `implementation-plan.md` §6 扩展触发条件 |
| 长视频、多工具合集、真人出镜 | 素材与拍摄链路不同 | 后续 |
| 分布式队列、消息中间件、外部数据库 | 单机 SQLite 足够；引入后运维成本高于收益 | 队列积压或跨机需求出现时 |
| 自动生成发布文案的事实性承诺 | 事实需证据链支撑 | 与 §12 同一门槛 |

### 1.3 MVP 约束与本设计选择（区分 README 事实与新增决定）

- `[已知]` 一个网页工具 / 一个使用场景 / 一条约 60 秒竖屏 / 一个固定模板 / 一台桌面执行机。
- `[已知]` 保留五个岗位：`researcher`、`operator`、`producer`、`reviewer`、`publisher`。
- `[已知]` 研究、素材收集可并行；桌面操作、录屏、GUI 剪辑互斥。
- `[已知]` **实测完成之后**才允许文案定稿。
- `[决定]` 首版选择 FFmpeg 作为可重建后端，具体渲染能力仍需验收；剪映/CapCut 暂不实现。
- `[决定]` 首版发布走人工明确批准的交接/通道；真实账号验收前不得声称自动发布可用。

## 2. 需求到模块映射

| # | 需求（README 出处） | 承接模块 | 关键验收点 |
| --- | --- | --- | --- |
| R1 | 工具发现与候选记录（§6.1） | `researcher` Agent + `discover-tools` Skill + `tool_candidates` 表 | 候选含官网、来源、核查日期、待验证项 |
| R2 | 素材收集与许可记录（§6.2） | `collect-assets` Skill + `assets`/`asset_licenses` + `library/` | 只有通过许可准入的素材可进入剪辑单（含 cleared/public_domain/first_party_owned） |
| R3 | 桌面独占与串行（§7.1） | `desktop_gateway` + `leases`/`epoch`（详见 doc2 §4） | 旧执行者被**真正终止**后才重新分配 |
| R4 | 实测与录屏（§7.2–7.3） | `operator` Agent + `record-tool-demo` Skill + OBS/Playwright 驱动 | 原片可解码、关键操作与结果确实出现 |
| R5 | 证据—旁白—时间轴血缘（§8.1） | `evidence`/`claims`/`script_lines`/`segments` + 触发器 | 无证据的断言不能冻结进文案 |
| R6 | 固定模板剪辑（§8.2） | `template-v1` + `edit_plan.json` + FFmpeg 渲染器 | 固定工具链验证字节确定性；跨环境验证预先定义的音画等价及血缘，不强求相同哈希 |
| R7 | 可替换剪辑后端（§8.3） | `render_backend` 接口 + 待实现的 `ffmpeg` 后端；其他后端只保留契约 | 适配器未验收时不得被选中 |
| R8 | 全片质检（§8.4） | `qa` 阶段：确定性全解码 + 全帧统计 + 全片视听覆盖 | 禁止以抽帧/转写替代完整看听 |
| R9 | 持久化与恢复（§9） | Python 协调器 + SQLite WAL + 内容寻址对象库 + 版本化 job 目录 | 杀进程后只重做受影响阶段 |
| R10 | 发布去重与对账（§10） | `publish_intents`/`external_operations`/`publications` + 对账流程 | 不确定提交不盲重试；最终状态核实 |
| R11 | 自动触发（§11） | 首版仅主聊天手动启动；`job.create` 是唯一入口，定时服务不实现 | 首版无调度器常驻触发 |
| R12 | 权限与凭证隔离（§5.4、§11） | 能力令牌 + 凭证外置 + 审批令牌 | 网页内容不能触发控制面操作 |

## 3. MVP 决策与取舍

每条含"选了什么 / 否了什么 / 为什么"。**技术选型以可验证性为先，不以功能齐全为先。**

### D1 单进程 Python 协调器，而非文件约定 + Agent 自律 `[决定]`

- **选**：一个常驻进程 `avcd`（Python 3.11+，仅标准库 + `sqlite3`），是数据库的**唯一写者**与租约编排方；桌面 epoch 权威在 gateway。
- **否**：靠 Agent 依次写 `task.json` 实现状态流转。原因：无原子性、无并发仲裁、无法拒绝越权写入、崩溃后无法判断"文件写了但状态没改"。
- **代价**：多一个进程要管理；Agent 必须通过 CLI/本地 socket 交互。可接受。

### D2 SQLite + 内容寻址对象库 + 小 JSON 清单 `[决定]`

- **选**：SQLite（WAL、`synchronous=FULL`、单写连接）存状态与索引；大文件进内容寻址库 `store/objects/<sha256[0:2]>/<sha256>`；job 目录只放小清单与指针。
- **否**：所有产物直接堆在 `jobs/<job>/`。原因：重试会产生同名覆盖、GC 无法判断引用、哈希校验与去重困难。
- **否**：Postgres。原因：单机、单写者，收益不足；迁移成本会推迟 R3/R9 的验证。

### D3 桌面网关（capability 隔离），而非"租约字段 + Agent 自觉" `[决定]`

- **选**：`desktop_gateway` 是桌面上唯一持有键鼠/录屏/浏览器控制能力的进程；Agent 运行在**没有输入设备与录制 API 权限**的执行环境中，只能通过 gateway 的本地接口操作。
- **否**：仅在数据库写一个 `lease_token`，由 Agent 自行检查。原因：令牌是建议，不是强制；暂停后恢复的旧会话会继续发事件。
- **代价**：需要一次性搭好进程隔离（独立用户/容器/cgroup、X11 或 Wayland 访问收敛到 gateway）。这是首版最值得投入的工程成本。

### D4 FFmpeg 为首版唯一计划实现的剪辑后端 `[决定]`

- **选**：`edit_plan.json`（声明式）+ `render_ffmpeg.py`（可重建）。成片 = 剪辑单 + 模板版本 + 素材哈希 + 渲染器版本 的函数。
- **否**：以剪映工程为主交付。原因：`[待验]` 无法保证无人值守导出；本项目尚未验证其工程重建与导出链路。
- **保留**：`render_backend` 接口契约；不为剪映/CapCut 提前创建无用的源码桩，任何适配器必须在 job 上显式通过 `backend_certification` 才能被调度。

### D5 首版发布 = 人工交接，代码里只有"意图 + 对账" `[决定]`

- **选**：`publisher` Agent 生成发布包（成片、封面、标题、简介、标签、声明），`publish_intents` 进入 `awaiting_human`；由**独立于 Agent 的人类身份**通过可信审批入口批准，再由人完成实际上传。TTY 和输入 ID 只是交互，不能充当人类身份认证。
- **否**：调用真实平台 API。原因：`[待验]` 权限与回执能力未验证；且首版要验证的是"不重复、可对账"，不是接口。
- **保留**：发布适配器的文档接口与幂等键契约，不提前创建平台源码桩，避免后续改动数据模型。

### D6 单桌面串行 + 研究并行，不引入通用 DAG 引擎 `[决定]`

- **选**：任务图由 `task_edges` 表 + 协调器调度循环承担；资源类只有 `desktop`（独占）、`render`（信号量 1）、`net`（限速）、`gc`（全局锁）。
- **否**：LangGraph/Celery/Temporal。原因：`[已知]` README 仅建议"需要图式编排时可评估"；首版任务数 < 30，引入后调试成本高于收益。保留事件契约，便于后续替换。

### D7 "证据等级"进数据库，而非只进 Markdown `[决定]`

- **选**：每条功能断言是 `claims` 行，`supported` 状态由触发器强制要求存在未隔离的证据行。文案冻结、发布授权都以此为前置。
- **原因**：仅靠评审自觉无法阻止"实测没做完就写稿"，也无法在版本变化后自动失效下游。

### D8 本地发送许可一次消费，未知结果必须对账 `[决定]`

- **选**：跨系统提交在外部无幂等保证时，承诺的是 **每个发送许可只能被原子领取一次 + 可审计的 UNKNOWN 状态 + 人工/查询对账**。这不等于一个发布计划永久最多发送一次：权威否定结果及重新授权可以产生新的尝试，但必须留存关联与依据，不能凭超时或普通 404 自动重提。
- **明确声明**：系统**不提供**跨平台严格 exactly-once。任何文档或 UI 措辞不得暗示可以。

## 4. 架构

### 4.1 进程与信任边界

```
[Agent 容器/受限用户]  researcher / operator / producer / reviewer / publisher
        │ 只有：avc CLI（Unix socket, SO_PEERCRED + capability token）、自己 attempt 目录、受限 MCP
        ▼
[avcd]  协调器 · 唯一写者 · SQLite WAL · 状态机/调度/预算/事件/对账
        │ 控制通道（仅 avcd 可连，带 token）
        ▼
[desktop_gateway]  持有 X11/Wayland 输入、OBS WS、Playwright CDP；epoch 权威（内存 + 磁盘 fsync）
        │
        ▼
[浏览器 / 被实测软件 / OBS]        [FFmpeg 渲染子进程]（独立，不需桌面）
store/objects（内容寻址，不可变）   var/state（db、journal、epoch）
```

`[决定]` 关键不变量：**Agent 进程永不直接拥有键鼠、屏幕捕获、OBS 控制或平台凭证**。它只有 `avc` 客户端与自己的 attempt 目录。这是 §10.1 注入防护与 R3 的技术基础，不是文档约定。

### 4.2 组件职责

| 组件 | 是什么 | 负责 | 明确不负责 |
| --- | --- | --- | --- |
| `avcd` | 常驻协调器（单写者） | 状态机、调度、租约与 fencing 编排、产物提交、预算、事件日志、对账驱动 | 不操作桌面、不渲染视频、不调外部 API |
| `avc` | CLI 薄客户端 | 命令封装、幂等键、JSON 输出 | 不缓存状态、不做业务判断 |
| `desktop_gateway` | 桌面能力代理 | 会话生命周期、epoch 权威、输入/录屏/CDP 执行、撤销与静默探测 | 执行任务范围与动作白名单，不代替人类判断高影响行为；不直接改业务表 |
| `render_ffmpeg` | 无状态渲染器 | 读 `edit_plan.json` → 出 MP4 + 中间报告 | 不做选片、不改剪辑单 |
| `qa_full` | 无状态质检器 | 全解码、全帧/全采样统计、字幕对齐、覆盖日志校验 | 不做主观审美判断 |
| Skill | 过程知识（`.agents/skills/*`） | 告诉 Agent 怎么做、按什么顺序、检查什么 | 不提供权限、不独立运行 |
| Agent | 岗位（`.codex/agents/*.toml`） | 判断、规划、调用工具、写产物 | 不直接改数据库、不直接碰桌面 |

### 4.3 部署形态

- 一台机器、一个 OS 用户跑 `avcd` 与 `desktop_gateway`，另一个受限用户/容器跑 Agent。
- 启动顺序：`avcd` → `desktop_gateway`（注册资源、自检 OBS/浏览器/输入）→ Agent 会话。
- `avcd` 崩溃：Agent 的 CLI 调用失败；已提交状态按持久化契约恢复，在途操作仍可能未知；重启后按 doc2 §4（租约回收）、§5（产物一致性）、§9（取消与重试）恢复。`desktop_gateway` 崩溃：所有活跃租约视为 `revoking`，重启后 epoch 从磁盘续增，绝不回退。
- 目录约定（增补 README §5.1，不冲突）：

```
auto-video/
├── src/auto_video/            # 协调器、网关、渲染器、QA 源码
├── docs/
├── templates/v1/              # 模板参数、字体、字幕样式、安全区
├── .agents/skills/            # README 既定
├── .codex/agents/             # README 既定
├── var/                       # 运行态，全部不入库
│   ├── state/av.db, av.db-wal
│   ├── state/journal/         # 外部操作预写日志（doc2 §8）
│   ├── state/desktop/epoch    # epoch 高水位，fsync
│   ├── store/objects/         # 内容寻址不可变对象
│   ├── store/tmp/             # 提交缓冲，同文件系统
│   └── logs/
└── jobs/<job_id>/             # 只放清单与指针，不放原始大文件
```

`[决定]` `var/`、完整 `jobs/`、`library/inbox/` 加入忽略规则；示例放不含真实数据的 `examples/`；凭证永不进入仓库（§10.2）。

## 5. 主流程

阶段：`discover → test → capture → script → edit → qa → publish → reconcile`，`collect` 为可并行依赖阶段。

```
主聊天(统筹)
  │ avc job create --spec ...            → job.state=planned
  ├─ 并行：discover(task A)  collect(task B)      resource: net
  │     └ 产物：candidate.json / asset+license
  ├─ 汇合：test（operator, resource=desktop）       ← 必须真实执行
  │     └ 产物：test_run + evidence(截图/结果文件/日志)
  ├─ capture（operator, resource=desktop）          ← 正式录屏，分 take
  │     └ 产物：raw takes + capture_manifest(关键时间点)
  ├─ script（producer）                             ← 硬门槛：存在 succeeded test_run
  │     └ claims 绑定 evidence → script.frozen
  ├─ edit（producer, resource=render）              ← 只能引用 license cleared 的素材
  │     └ edit_plan v1 → render → video_version v1
  ├─ qa（reviewer，独立上下文）                      ← 全解码 + 全片视听
  │     └ qa_run + approval（绑定 mp4 sha256）
  ├─ publish（publisher）                            ← 生成包 + 意图，等待人工
  │     └ publish_intent(awaiting_human) → human approve → external_op
  └─ reconcile（publisher/统筹）
        └ publications 终态 + receipt
```

**并行规则（硬性）**：`discover`/`collect`/`script` 草稿/`qa` 的确定性部分不需桌面，可并行；`test`/`capture`/GUI 剪辑/人工桌面介入必须持有 `desktop:local-01` 租约，串行。`render` 与桌面可并行，但需实测 CPU 竞争影响（doc4 §5.4），超标则降为串行。

**定稿顺序（硬性）**：`script.frozen` 需要 `script_test_basis` 至少一条 succeeded `test_run`，且所有 `requires_evidence=1` 的 claim 为 `supported`。触发器强制，见 doc2 §7.

## 6. Agent / Skill / 工具边界

### 6.1 权限矩阵（能力令牌按角色签发）

| 能力 | researcher | operator | producer | reviewer | publisher | 统筹(主聊天) |
| --- | --- | --- | --- | --- | --- | --- |
| 读任务与产物元数据 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| 写候选/素材/来源 | ✓ | | | | | |
| 提交 evidence / test_run | | ✓ | | | | |
| 申请桌面租约 | | ✓ | | | | |
| 提交 script / edit_plan | | | ✓ | | | |
| 提交 qa_run | | | | ✓ | | |
| 签发 qa_approval | | | | ✓(仅本 job，仅 pass) | | |
| 创建 publish_intent | | | | | ✓ | |
| **批准发布** | | | | | | ✗（独立可信人类身份） |
| 调外部网络（受限域） | ✓ | ✓ | | | | |
| 改预算上限 | | | | | | ✗（仅人类） |

`[决定]` 令牌在 `avcd` 内校验，绑定 `(attempt_id, role, expiry, capability set, task_scope)`，限定目标应用、窗口、域名和允许动作；客户端不能自报身份或扩大范围；令牌不写入日志明文（只存摘要）。角色升级需要新 attempt，不能在会话中途提权。

### 6.2 工具边界（Agent 可用的全部能力）

1. `avc` 子命令：`claim/heartbeat/commit/submit/query` 等，全部走 socket。
2. 受限 MCP：`web_fetch`（域白名单 + 结果标记 `origin=external`）、`obs_ctl`（**仅转发给 gateway**，自身无 WS 权限）、`media_probe`（只读）。
3. 文件：仅 `jobs/<job>/<stage>/attempt-<n>/` 与 `library/inbox/` 的读写。

`[决定]` 不存在"Agent 直接执行 shell 操作桌面"的通道。若运行时提供通用 shell，桌面能力仍由 OS 权限隔离（无输入设备、无显示服务凭据），使其无效。

### 6.3 Skill 与代码的分工

- Skill 只写**判断与检查顺序**（读什么、先验证什么、失败怎么停）。
- 任何会产生副作用的动作必须走 `avc` 命令，命令内部做状态与权限校验。
- Skill 中不得包含凭证、写死的机器绝对路径或未验证的环境假设或"跳过校验"的应急分支。反例：`record-tool-demo` 若写"若 StartRecord 无响应则重发"，必须改为"查询状态，未知则停"。

## 7. 协同模型

- **交接物是数据库行 + 产物哈希，不是聊天记录**。子 Agent 的完成消息只是提示；`avcd` 以 `attempt.state=succeeded` 且 `outputs_digest` 校验通过为准。
- **plan_revision**：统筹可提出调整；改变预算、目标账号或授权范围须经可信人类入口批准，再产生新 `plan_revisions` 行；任务绑定 revision。旧 revision 的未完成 attempt 被标 `stale`，不能提交产物。
- **冲突**：同一 task 至多一个 `running` attempt（唯一索引强制）；同一 artifact 不可覆盖（doc2 §2.2 的 `UNIQUE(sha256)` 与 §5 的提交协议）。
- **人工中断**：`avc pause job` 停止派发新任务，允许在飞 attempt 完成或按取消契约终止；恢复需显式 `avc resume`。
- **质检独立**：`reviewer` 用独立 attempt 与独立读取路径，只能看 `video_version` 指向的实际 MP4 与证据表，不读 producer 的自我说明作为通过依据（说明可作为 finding 输入，不作为证据）。

## 8. 接口与事件契约

### 8.1 命令（变更命令要求幂等键，纯查询无需幂等键）

| 命令 | 主体 | 语义要点 |
| --- | --- | --- |
| `job.create` / `job.pause` / `job.resume` | 统筹/人类 | 创建时写预算账户 |
| `task.list` / `task.claim` | 任意 | claim 返回 attempt_id + 令牌 + 租约票据（若需桌面） |
| `task.heartbeat` | 执行者 | 刷新 `heartbeat_at`；不刷新不延长租约 |
| `artifact.commit` | 执行者 | 声明 `(sha256, kind, rel_path)`；服务端校验存在性与哈希 |
| `stage.complete` | 执行者 | 提交 `outputs_digest` 与检查点；由状态机决定接受/拒绝 |
| `lease.acquire` / `lease.release` | operator | 触发撤销编排（doc2 §4） |
| `budget.reserve` / `budget.settle` | 执行者 | 预留不足则拒绝准入 |
| `qa.approve` | reviewer | 需 `qa_run=pass` 且覆盖日志达标 |
| `publish.intent` | publisher | 需有效 `qa_approval` |
| `publish.approve` | **独立可信人类身份** | 服务端签发一次性审批，绑定不可变发布包哈希（视频、封面、文案、标签、声明）、平台、账号、计划时刻和 revision；任何改变均重新审批 |
| `external.submit` | publisher | 只允许在 `human_approval` 有效时；写预写日志后发起 |
| `external.reconcile` | publisher | 记录查询结果，推动 UNKNOWN 收敛 |

响应统一：

```json
{"ok": true, "data": {...}, "event_seq": 1043, "warnings": []}
{"ok": false, "error": {"code": "FENCED", "message": "...", "retryable": false, "detail": {}}}
```

错误码集合（封闭）：`INVALID_TRANSITION`、`FENCED`、`STALE_REVISION`、`HASH_MISMATCH`、`BUDGET_EXCEEDED`、`NEEDS_INPUT`、`UNKNOWN_EXTERNAL`、`AUTH_REQUIRED`、`UNAUTHORIZED_CAPABILITY`、`CONFLICT`、`DEPENDENCY_UNSATISFIED`。

### 8.2 事件

`events` 追加写、不可改删。类型命名 `entity.action`，如 `attempt.started`、`artifact.committed`、`lease.fenced`、`external.unknown`、`qa.invalidated`。

```json
{"seq": 1043, "event_id": "evt_01JABCD...", "job_id": "video-001", "type": "lease.fenced",
 "entity": {"type": "lease", "id": "desktop:local-01"},
 "actor": {"kind": "agent", "role": "operator", "attempt_id": "at_5c"},
 "payload": {"old_epoch": 11, "new_epoch": 12, "revoked_pid": 4812, "quiesce_probe_ms": 1500},
 "created_at": "2026-11-02T09:41:07.213Z"}
```

`[决定]` 事件是审计与恢复的事实来源；业务表是当前状态的物化。两者在同一 SQLite 事务内提交，不允许只写其一。

## 9. 恢复流程

| 中断点 | 检测方式 | 动作 | 不允许 |
| --- | --- | --- | --- |
| `avcd` 崩溃 | 重启扫 `attempts.state=running` 且 `heartbeat_at` 超 `heartbeat_deadline` | 置 `orphaned`，撤销其桌面租约（若持有），仅释放可证明未发生费用的预留；UNKNOWN 费用保持占用直到对账 | 直接重跑同一 attempt |
| 执行者失联（进程死/挂起） | 心跳超时 → gateway 探活 | 网关先拒绝旧授权并持久化递增 epoch → drain/cancel 在途操作 → 确认旧进程终止并封存录制 → 资源恢复 free 后才授新 attempt；探测超时不是死亡证据 | 仅因 TTL 到期就换人 |
| 提交产物时崩溃 | `store/tmp` 残留 + 无对应 `artifact_refs` 行 | 启动时移入 `store/tmp/quarantine/`，超期 GC | 宣布产物成功 |
| 数据库回滚/恢复旧备份 | `leases.epoch` < gateway 磁盘高水位 | gateway 拒绝所有低 epoch 请求，`needs_input` | 重启后重置 epoch |
| 录屏中 OBS 断开 | gateway 收到 WS close | 收尾已知文件，校验可解码，未覆盖镜头标 `missing` | 直接重录整段覆盖原片 |
| 渲染中断 | 渲染器原子输出 + 报告缺失 | 从 `edit_plan` 重渲（确定性），旧版本保留 | 修改剪辑单以"修复" |
| 外部提交无响应 | 预写日志有 `SUBMITTING` 无结果 | 进 `UNKNOWN`，禁止重提，走对账 | 重发请求 |
| 登录/验证码 | 网关检测到挑战页 | `needs_input`，保存现场截图 | 自动尝试绕过 |

`[决定]` 恢复的粒度是 **stage**，不是 job：已完成且校验通过的产物永不重算，除非其 `inputs_digest` 变化（此时是版本问题，不是恢复问题）。

## 10. 安全与内容权利

### 10.1 信任边界与提示注入

- 外部内容（网页、素材内文字、OCR、字幕、模型输出）一律标 `origin=external`，只进**数据面**。
- 控制面操作**只由任务配置与人类审批授权**：`publish.approve`、`budget.set_limit`、`plan.revise` **不接受 Agent 令牌**，只接受独立审批用户/可信 UI 的认证身份。MVP 使用与 Agent 不同的 OS UID 和受限审批 socket（peer credentials 校验）；Agent 不可访问该身份凭据、socket 或以其身份启动进程。TTY 和二次输入只作防误触。若环境不能隔离审批入口，则禁止自动提交，改成人工交接。服务端写 `human_approvals` 及对应事件，不能信任客户端传入的 human 标记。
- `[决定]` 具体反注入机制：
  1. `external` 字符串在进入提示前加引用边界（这不是安全沙箱），并在协调器侧对 `authorization` 类字段做来源校验——任何命令如果携带 `origin=external` 的目标账号/预算/发布参数，直接 `UNAUTHORIZED_CAPABILITY`。
  2. 网页中出现的"请执行/请忽略"文本不得改变 `plan_revision`；变更需新 revision 由统筹发起。
  3. Agent 的 shell/MCP 无桌面能力（§4.1），但 operator 仍能经 gateway 请求合法桌面动作，语义注入仍可能误导它。gateway 必须校验任务/目标/动作范围；付款、发布、凭证、授权变更等高影响操作走独立批准，普通截图或网页提示不能提升能力。不得声称引用标记或网关可消除提示注入。
- 浏览器下载物进 `library/inbox/`，先做类型嗅探与哈希，未登记不得被 render 引用。

### 10.2 凭证

- 存于 OS keychain 或 `~/.config/auto-video/credentials.d/<name>`（权限 600），DB 只存 `credential_ref`（名称）。
- 只有 `desktop_gateway` 与 `publisher` 的**外部适配器进程**读取；Agent 会话拿不到。
- 日志与事件经统一脱敏过滤器（键名 + 值模式双重），`avc` 输出默认脱敏。

### 10.3 内容权利

- 每个进入剪辑单的素材必须有 `asset_licenses` 行且 `status ∈ {cleared, public_domain, first_party_owned}`；`render_ffmpeg` 解析剪辑单时硬拒绝其余状态。
- 记录许可条款快照（当时的页面文本或官方条款 PDF）路径与哈希；`[已知]` 价格与条款需当期复核，不能沿用旧记录。
- 实测录屏中的第三方内容（网页中的图片/视频）需标注，必要时裁切或遮挡；参考片中的第三方画面不得当作本方实测素材使用。
- 成片与发布包附"素材来源与许可清单"，作为 `video_version` 的伴随产物。

## 11. 观测与成本

### 11.1 观测（MVP 只做这三类，避免过度建设）

1. **状态可查**：`avc job status --json` 输出任务树、依赖、阻塞原因、预算余量。
2. **事件可追**：`events` 按 job 导出 JSONL；关键指标来自事件派生，不额外埋点：阶段停留时长、attempt 失败率、fence 次数、UNKNOWN 数量、重渲次数。
3. **产物可核**：每条成片可追溯 `video_version → edit_plan → segments → script_lines → claims → evidence → test_run → tool`。

日志分级：`avcd` 结构化 JSON 到 `var/logs/avcd.jsonl`，按 7 天滚动；敏感字段脱敏；不放提示全文（只放摘要与哈希）。

### 11.2 成本

- 计量单位：微美元整数（`*_micros`），避免浮点误差。
- 类别：`llm`、`tool_api`、`media_gen`、`render_compute`、`storage`、`human`。
- `[决定]` 成本控制发生在**准入**，不是事后统计：每次有费用的操作以独立 reservation ID 原子预留，已知最大输出/价格/调用次数才可定义硬上限；只能预测的供应商使用明确的软预算和超额告警，不宣称绝不超支。结算/释放按 reservation 幂等，UNKNOWN 保留潜在费用，不能以 p50 估计作为硬限制（详见 doc2 §11）。
- `[假设]` 单条 60 秒视频的直接成本（模型调用 + 少量 API + 本地渲染电费）量级不能由本文可靠推断，不作为预算默认值；**本文不给出具体供应商价格**，因为未联网核验。实施时由人类在 job 配置中填写。
- 预算耗尽：任务进 `budget_blocked`，不发新 attempt，已有产物保留，等待人类追加或收敛范围。

## 12. 风险登记（设计层面）

| 风险 | 影响 | 缓解 | 状态 |
| --- | --- | --- | --- |
| Agent 运行时无法做到完全无桌面权限 | D3 隔离失效 | 先验证独立 OS 用户/容器与控制通道隔离；失败则阻断无人值守桌面，人工监督模式单独验收，不宣称独占或 fencing 有效 | `[待验]` |
| Codex 子 Agent 工具继承与预期不符 | 角色无法调用 `avc` | 阶段一先做最小 Agent 冒烟 | `[待验]` |
| Playwright 录屏与系统声不同步 | 音画对不齐 | 首版优先用 OBS 录系统声；Playwright 只作画面来源 | `[待验]` |
| FFmpeg 中文字体/字幕渲染差异 | 跨机不一致 | 字体随模板入库并校验哈希；渲染机固定 | `[决定]` |
| 模型对全片审看能力有限 | 质检漏判 | 确定性统计兜底 + 人工校准 + 覆盖日志（doc4 §5） | `[待验]` |
| 平台规则变化 | 发布失败/下架 | 人工交接 + 发布前规则复核步骤 | `[待验]` |

## 13. 与 README 的关系

本文不修改 README 中的任何历史事实与核查结论；仅在其"待实施结构"之上给出可执行设计。README 中标注"尚未实现或验收"的状态在本设计完成后**仍然成立**。
