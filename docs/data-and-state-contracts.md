# 数据与状态契约

版本：v0.2（审查修订设计稿）· 2026-11-02 · 配套：[架构](technical-design.md)、[实施](implementation-plan.md)、[验收](validation-plan.md)、[review](review-report.md)

定义 SQLite 关系模型、状态机、租约 fencing 的**实际强制手段**、落盘一致性、血缘、外部对账、幂等与取消契约。
所有 SQL/伪代码是**契约示例**，不是已实现代码。方言 SQLite。每个连接显式执行 `PRAGMA foreign_keys=ON; PRAGMA recursive_triggers=ON;`；协调器连接另外设置 `PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;`。迁移、参数化事务与守卫需共同实现；下列示例不等于已完成所有权限隔离。

## 1. 建模原则

1. **单一写者**：只有 `avcd` 写库；Agent 经 `avc` 提交，由协调器在一个事务内落库。
2. **不可变产物、可变状态**：产物内容寻址后永不改写；不可变版本对象用版本号；任务、租约及审批使用状态比较更新（CAS）和事件序号，不假定每张表都有 `version` 字段。
3. **状态与事件同事务**：业务表变更必须伴随 `events` 插入（同一 `BEGIN IMMEDIATE`）。
4. **守卫分层**：数据库约束保证结构性不变量；身份、文件路径、跨进程与外部请求校验由协调器和网关强制。不能把 SQL 约束或 Agent 提示词当作完整权限边界。
5. **审计时间统一 UTC** ISO-8601 毫秒。租约与退避在进程内用 monotonic 时钟，事件序号表示提交顺序；单线程不能防止系统时钟回拨。重启后不可复用旧 monotonic 截止值，先隔离旧会话、对账，再建立新租约。

## 2. 关系模型

### 2.1 任务与计划

```sql
CREATE TABLE jobs (
  job_id TEXT PRIMARY KEY, title TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('planned','running','paused','blocked','done','cancelled')),
  current_revision INTEGER NOT NULL DEFAULT 1,
  target_platforms TEXT NOT NULL,        -- JSON ["douyin","xiaohongshu"]
  target_account TEXT,                   -- 账号逻辑名，非凭证
  template_id TEXT NOT NULL,             -- 'template-v1'
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE plan_revisions (
  job_id TEXT NOT NULL REFERENCES jobs(job_id), revision INTEGER NOT NULL,
  spec TEXT NOT NULL,                    -- JSON: 选题/范围/预算/发布模式
  reason TEXT NOT NULL, author TEXT NOT NULL,   -- 'human' | 'orchestrator'
  created_at TEXT NOT NULL, PRIMARY KEY (job_id, revision));

CREATE TABLE tasks (
  task_id TEXT PRIMARY KEY,              -- 'video-001/capture/shot-03'
  job_id TEXT NOT NULL REFERENCES jobs(job_id),
  stage TEXT NOT NULL CHECK (stage IN
    ('discover','collect','test','capture','script','edit','qa','publish','reconcile')),
  kind TEXT NOT NULL, revision INTEGER NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('pending','ready','running','retry_wait','succeeded',
    'needs_input','failed','cancelled','stale','budget_blocked')),
  requires TEXT NOT NULL DEFAULT '{}',   -- {"desktop":true,"render":true}
  inputs_digest TEXT,                    -- 输入产物集合的规范化哈希
  max_attempts INTEGER NOT NULL DEFAULT 2, attempt_count INTEGER NOT NULL DEFAULT 0,
  priority INTEGER NOT NULL DEFAULT 100, not_before TEXT,
  error_class TEXT, last_error TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE task_edges (
  from_task TEXT NOT NULL REFERENCES tasks(task_id),
  to_task TEXT NOT NULL REFERENCES tasks(task_id),
  dep_kind TEXT NOT NULL CHECK (dep_kind IN ('artifact','gate','soft')),
  PRIMARY KEY (from_task, to_task), CHECK (from_task <> to_task));

CREATE TABLE attempts (
  attempt_id TEXT PRIMARY KEY,           -- 'at_5c' (ULID)
  task_id TEXT NOT NULL REFERENCES tasks(task_id), attempt_no INTEGER NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('running','succeeded','failed','cancelled','orphaned','stale')),
  executor TEXT NOT NULL,                -- 'agent:operator'
  session_id TEXT NOT NULL, pid INTEGER, -- Codex 会话/进程身份
  capability TEXT NOT NULL,              -- 能力集合的哈希引用
  heartbeat_at TEXT NOT NULL, deadline_at TEXT NOT NULL, outputs_digest TEXT,
  checkpoint TEXT,                       -- JSON 恢复点
  created_at TEXT NOT NULL, ended_at TEXT, UNIQUE (task_id, attempt_no));
CREATE UNIQUE INDEX ux_attempt_running ON attempts(task_id) WHERE state='running';
```

`[决定]` `ux_attempt_running` 是"一个任务同时只有一个执行者"的**数据库级**保证，不依赖调度器逻辑。

### 2.2 资产、许可与产物引用

```sql
CREATE TABLE assets (
  asset_id TEXT PRIMARY KEY,
  sha256 TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('raw_video','image','audio','font','result_file','screenshot',
    'script','subtitle','cover','mp4','edit_plan','report')),
  bytes INTEGER NOT NULL CHECK (bytes >= 0), storage TEXT NOT NULL DEFAULT 'object_store',
  rel_path TEXT NOT NULL,                -- 'objects/ab/abcdef...'
  media TEXT,                            -- JSON {duration_ms,w,h,fps,audio_codec}
  origin TEXT NOT NULL CHECK (origin IN ('first_party','third_party','generated')),
  created_at TEXT NOT NULL, UNIQUE (sha256));   -- 内容寻址：同内容只有一行

CREATE TABLE asset_licenses (
  license_id TEXT PRIMARY KEY, license_name TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('cleared','public_domain','first_party_owned',
    'pending','restricted','unknown')),
  terms_text TEXT NOT NULL,              -- 当时条款要点摘录
  verified_by TEXT NOT NULL, verified_at TEXT NOT NULL, expires_at TEXT);

CREATE TABLE asset_sources (
  asset_id TEXT PRIMARY KEY REFERENCES assets(asset_id),
  source_url TEXT, author TEXT, publisher TEXT, retrieved_at TEXT NOT NULL,
  license_id TEXT REFERENCES asset_licenses(license_id),
  snapshot_path TEXT, snapshot_sha256 TEXT);       -- 条款页面快照

CREATE TABLE asset_usage_rules (
  license_id TEXT PRIMARY KEY REFERENCES asset_licenses(license_id),
  allow_commercial INTEGER NOT NULL CHECK (allow_commercial IN (0,1)),
  require_attribution INTEGER NOT NULL CHECK (require_attribution IN (0,1)),
  allow_modification INTEGER NOT NULL CHECK (allow_modification IN (0,1)),
  forbidden_contexts TEXT);                        -- JSON ['endorsement']

CREATE TABLE artifact_refs (             -- attempt 与本次提交产物的绑定
  attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
  sha256 TEXT NOT NULL REFERENCES assets(sha256),
  stage_role TEXT NOT NULL,              -- 'deliverable'|'evidence'|'intermediate'
  created_at TEXT NOT NULL, PRIMARY KEY (attempt_id, sha256, stage_role));
```

`[决定]` GC 不依赖冗余引用计数，直接查 `artifact_refs`/`segments`/`video_versions` 的外键，避免计数漂移。

### 2.3 实测、证据、文案、时间轴（血缘链）

```sql
CREATE TABLE tool_candidates (
  tool_id TEXT PRIMARY KEY, tool_name TEXT NOT NULL, homepage TEXT NOT NULL,
  discovered_from TEXT NOT NULL, checked_on TEXT NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('selected','rejected','deferred')), reject_reason TEXT);

CREATE TABLE test_runs (
  test_run_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(job_id),
  tool_id TEXT NOT NULL REFERENCES tool_candidates(tool_id), tool_version TEXT NOT NULL,
  environment TEXT NOT NULL,             -- JSON: OS/浏览器/界面版本/输入法
  input_sha256 TEXT NOT NULL REFERENCES assets(sha256),
  started_at TEXT NOT NULL, ended_at TEXT,
  result TEXT NOT NULL CHECK (result IN ('success','partial','failure','blocked')),
  outcome_note TEXT NOT NULL,            -- 事实描述，不含营销措辞
  succeeded INTEGER NOT NULL CHECK (succeeded IN (0,1)),
  CHECK (succeeded = (result='success')),
  CHECK (ended_at IS NULL OR ended_at >= started_at));
CREATE INDEX ix_test_input ON test_runs(tool_id, tool_version, input_sha256, started_at);
-- 同日同输入允许复测；身份由 test_run_id 区分，不按日期去重。

CREATE TABLE evidence (
  evidence_id TEXT PRIMARY KEY, test_run_id TEXT NOT NULL REFERENCES test_runs(test_run_id),
  asset_id TEXT NOT NULL REFERENCES assets(asset_id),
  kind TEXT NOT NULL CHECK (kind IN ('screen','result_file','log','video_clip','network_trace')),
  captured_at TEXT NOT NULL, t_start_ms INTEGER, t_end_ms INTEGER,  -- 录屏内区间
  description TEXT NOT NULL,
  quarantined INTEGER NOT NULL DEFAULT 0 CHECK (quarantined IN (0,1)),
  CHECK (t_end_ms IS NULL OR t_start_ms IS NULL OR t_end_ms > t_start_ms));

CREATE TABLE capture_manifests (
  capture_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(job_id),
  test_run_id TEXT NOT NULL REFERENCES test_runs(test_run_id),
  shots TEXT NOT NULL,                   -- JSON [{shot,asset_sha256,t_in,t_out,real_elapsed_ms}]
  gaps TEXT, checks TEXT NOT NULL);      -- JSON: 缺失镜头 / decode,audio,duration 检查

CREATE TABLE claims (                    -- 可被断言的功能结论
  claim_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(job_id), text TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('capability','pricing','availability','comparison','limit')),
  requires_evidence INTEGER NOT NULL DEFAULT 1 CHECK (requires_evidence IN (0,1)),
  status TEXT NOT NULL CHECK (status IN ('draft','supported','unsupported','retired')),
  checked_on TEXT,                       -- pricing/availability 必须当期复核
  UNIQUE (job_id, text));

CREATE TABLE claim_evidence (
  claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
  relation TEXT NOT NULL CHECK (relation IN ('supports','contradicts')),
  PRIMARY KEY (claim_id, evidence_id));

CREATE TABLE script_versions (
  script_version_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(job_id),
  version INTEGER NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('draft','frozen','superseded','invalidated')),
  frozen_at TEXT, inputs_digest TEXT NOT NULL,   -- 参与定稿的 test_run/claims 集合哈希
  UNIQUE (job_id, version));

CREATE TABLE script_test_basis (         -- 定稿所依据的实测记录
  script_version_id TEXT NOT NULL REFERENCES script_versions(script_version_id),
  test_run_id TEXT NOT NULL REFERENCES test_runs(test_run_id),
  role TEXT NOT NULL CHECK (role IN ('primary','supporting')),
  PRIMARY KEY (script_version_id, test_run_id));

CREATE TABLE script_lines (
  line_id TEXT PRIMARY KEY, script_version_id TEXT NOT NULL REFERENCES script_versions(script_version_id),
  seq INTEGER NOT NULL, text TEXT NOT NULL, start_ms INTEGER, end_ms INTEGER,
  UNIQUE (script_version_id, seq));

CREATE TABLE line_claims (
  line_id TEXT NOT NULL REFERENCES script_lines(line_id),
  claim_id TEXT NOT NULL REFERENCES claims(claim_id), PRIMARY KEY (line_id, claim_id));

CREATE TABLE edit_plans (
  edit_plan_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(job_id), version INTEGER NOT NULL,
  script_version_id TEXT NOT NULL REFERENCES script_versions(script_version_id),
  template_id TEXT NOT NULL, template_sha256 TEXT NOT NULL,
  renderer TEXT NOT NULL,                -- 'ffmpeg'|'jiaying'|'capcut'
  renderer_version TEXT NOT NULL, plan_json TEXT NOT NULL, plan_sha256 TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('draft','validated','rendered','invalidated')),
  UNIQUE (job_id, version));

CREATE TABLE segments (                  -- 时间轴片段 = 血缘最小单元
  segment_id TEXT PRIMARY KEY, edit_plan_id TEXT NOT NULL REFERENCES edit_plans(edit_plan_id),
  seq INTEGER NOT NULL, timeline_in_ms INTEGER NOT NULL, timeline_out_ms INTEGER NOT NULL,
  source_asset_id TEXT REFERENCES assets(asset_id), source_in_ms INTEGER, source_out_ms INTEGER,
  line_id TEXT REFERENCES script_lines(line_id), claim_id TEXT REFERENCES claims(claim_id),
  text_overlay TEXT,
  CHECK (timeline_out_ms > timeline_in_ms),
  CHECK (source_in_ms IS NULL OR source_out_ms > source_in_ms),
  UNIQUE (edit_plan_id, seq));
```

`[决定]` `segments` 是"证据 → 旁白 → 时间轴"链的落地位置：挂 `claim_id` 的镜头，其源素材必须属于该 claim 支撑的 evidence，由 §7 触发器校验。

### 2.4 视频版本、质检、审批

```sql
CREATE TABLE video_versions (
  video_version_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(job_id), version INTEGER NOT NULL,
  edit_plan_id TEXT NOT NULL REFERENCES edit_plans(edit_plan_id),
  mp4_asset_id TEXT NOT NULL REFERENCES assets(asset_id),
  subtitle_asset_id TEXT, cover_asset_id TEXT, voice_asset_id TEXT,
  state TEXT NOT NULL CHECK (state IN ('rendered','qa_passed','qa_failed','approved','invalidated','superseded')),
  invalid_reason TEXT, UNIQUE (job_id, version));

CREATE TABLE qa_runs (
  qa_run_id TEXT PRIMARY KEY, video_version_id TEXT NOT NULL REFERENCES video_versions(video_version_id),
  attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
  mp4_sha256 TEXT NOT NULL,              -- 冗余存储，便于跨表一致性校验
  verdict TEXT NOT NULL CHECK (verdict IN ('pass','fail','needs_human')),
  checks TEXT NOT NULL,                  -- JSON 每项检查结果与证据路径
  full_decode_ok INTEGER NOT NULL CHECK (full_decode_ok IN (0,1)),
  coverage_ratio REAL NOT NULL CHECK (coverage_ratio BETWEEN 0 AND 1), -- 审看区间并集覆盖比例
  report_asset_id TEXT NOT NULL REFERENCES assets(asset_id), created_at TEXT NOT NULL,
  CHECK (verdict<>'pass' OR (full_decode_ok=1 AND coverage_ratio=1)));

CREATE TABLE qa_approvals (
  approval_id TEXT PRIMARY KEY, qa_run_id TEXT NOT NULL REFERENCES qa_runs(qa_run_id),
  video_version_id TEXT NOT NULL REFERENCES video_versions(video_version_id),
  mp4_sha256 TEXT NOT NULL, plan_revision INTEGER NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('valid','invalidated')), invalid_reason TEXT,
  issued_at TEXT NOT NULL);
CREATE UNIQUE INDEX ux_approval_live ON qa_approvals(video_version_id) WHERE state='valid';
```

### 2.5 发布、外部操作、预算、租约、事件

```sql
CREATE TABLE publish_intents (
  intent_id TEXT PRIMARY KEY, video_version_id TEXT NOT NULL REFERENCES video_versions(video_version_id),
  platform TEXT NOT NULL, account_ref TEXT NOT NULL,
  package_asset_id TEXT NOT NULL REFERENCES assets(asset_id), -- 不可变发布包清单
  package_sha256 TEXT NOT NULL REFERENCES assets(sha256),
  plan_revision INTEGER NOT NULL,
  dedupe_key TEXT NOT NULL,              -- sha256(video_sha256|platform|account|plan)
  scheduled_at TEXT, mode TEXT NOT NULL CHECK (mode IN ('human_handoff','api','gui')),
  state TEXT NOT NULL CHECK (state IN ('draft','awaiting_human','approved','submitting','accepted',
    'reviewing','published','rejected','failed','unknown','cancelled')),
  UNIQUE (platform, account_ref, dedupe_key));   -- 本地去重硬约束

CREATE TABLE human_approvals (
  approval_token_id TEXT PRIMARY KEY, intent_id TEXT NOT NULL REFERENCES publish_intents(intent_id),
  dedupe_key TEXT NOT NULL, mp4_sha256 TEXT NOT NULL,
  package_sha256 TEXT NOT NULL REFERENCES assets(sha256),
  plan_revision INTEGER NOT NULL,
  approved_by TEXT NOT NULL,             -- 已认证的人类 principal，非 Agent 自报用户名
  approval_channel TEXT NOT NULL,        -- 独立受信任管理端，TTY 仅可作审计信息
  scope TEXT NOT NULL,                   -- canonical JSON: platform/account/scheduled_at
  used_at TEXT, revoked_at TEXT, expires_at TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE UNIQUE INDEX ux_unused_human_approval ON human_approvals(intent_id)
 WHERE used_at IS NULL AND revoked_at IS NULL;

CREATE TABLE external_operations (       -- 预写日志的 DB 镜像
  op_id TEXT PRIMARY KEY, intent_id TEXT REFERENCES publish_intents(intent_id),
  task_id TEXT REFERENCES tasks(task_id), provider TEXT NOT NULL, operation TEXT NOT NULL,
  idempotency_key TEXT NOT NULL, request_digest TEXT NOT NULL,
  retry_of TEXT REFERENCES external_operations(op_id),
  negative_evidence_asset_id TEXT REFERENCES assets(asset_id),
  send_claim_id TEXT UNIQUE,             -- 原子 CAS 后只向当前发送者交付一次
  approval_token_id TEXT REFERENCES human_approvals(approval_token_id),
  state TEXT NOT NULL CHECK (state IN ('PREPARED','SUBMITTING','ACCEPTED','RETURNED','UNKNOWN','FAILED','RECONCILED')),
  external_id TEXT, response_digest TEXT, submitted_at TEXT, resolved_at TEXT,
  reconcile_attempts INTEGER NOT NULL DEFAULT 0,
  UNIQUE (provider, idempotency_key),
  CHECK (retry_of IS NULL OR negative_evidence_asset_id IS NOT NULL));
CREATE UNIQUE INDEX ux_one_explicit_retry ON external_operations(retry_of) WHERE retry_of IS NOT NULL;

CREATE TABLE publications (
  publication_id TEXT PRIMARY KEY, intent_id TEXT NOT NULL REFERENCES publish_intents(intent_id),
  platform TEXT NOT NULL, account_ref TEXT NOT NULL, work_id TEXT, work_url TEXT, published_at TEXT,
  final_state TEXT NOT NULL CHECK (final_state IN ('draft','private','reviewing','public','rejected','deleted','unknown')),
  verified_by TEXT NOT NULL, verified_at TEXT NOT NULL,
  receipt_asset_id TEXT REFERENCES assets(asset_id),
  requires_correction INTEGER NOT NULL DEFAULT 0 CHECK (requires_correction IN (0,1)));

CREATE TABLE budget_accounts (
  job_id TEXT PRIMARY KEY REFERENCES jobs(job_id),
  limit_micros INTEGER NOT NULL CHECK (limit_micros >= 0),
  mode TEXT NOT NULL CHECK (mode IN ('hard','soft')),
  currency TEXT NOT NULL DEFAULT 'USD');

CREATE TABLE budget_reservations (
  reservation_id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES budget_accounts(job_id),
  attempt_id TEXT REFERENCES attempts(attempt_id),
  operation_key TEXT NOT NULL,            -- 一次有费用的操作，不是整个 stage 的 p50
  category TEXT NOT NULL CHECK (category IN ('llm','tool_api','media_gen','render_compute','storage','human')),
  bound_micros INTEGER NOT NULL CHECK (bound_micros >= 0),
  bound_kind TEXT NOT NULL CHECK (bound_kind IN ('enforced_max','estimate')),
  bound_basis TEXT NOT NULL,             -- 价格版本、token/时长/数量上限与供应商限额证据
  state TEXT NOT NULL CHECK (state IN ('held','unknown','settled','released')),
  actual_micros INTEGER CHECK (actual_micros >= 0),
  settlement_key TEXT UNIQUE,
  external_op_id TEXT REFERENCES external_operations(op_id),
  created_at TEXT NOT NULL, resolved_at TEXT,
  UNIQUE (job_id, operation_key),
  CHECK ((state='settled' AND actual_micros IS NOT NULL AND settlement_key IS NOT NULL)
      OR (state<>'settled' AND actual_micros IS NULL AND settlement_key IS NULL)));

CREATE VIEW budget_totals AS
 SELECT a.job_id, a.limit_micros, a.mode,
   COALESCE(SUM(CASE WHEN r.state IN ('held','unknown') THEN r.bound_micros ELSE 0 END),0) AS reserved_micros,
   COALESCE(SUM(CASE WHEN r.state='settled' THEN r.actual_micros ELSE 0 END),0) AS spent_micros
 FROM budget_accounts a LEFT JOIN budget_reservations r ON r.job_id=a.job_id GROUP BY a.job_id;

CREATE TRIGGER trg_budget_admit BEFORE INSERT ON budget_reservations BEGIN
 SELECT RAISE(ABORT,'reservation must start held') WHERE NEW.state<>'held';
 SELECT RAISE(ABORT,'hard budget requires enforced maximum')
  WHERE (SELECT mode FROM budget_accounts WHERE job_id=NEW.job_id)='hard' AND NEW.bound_kind<>'enforced_max';
 SELECT RAISE(ABORT,'budget exceeded') WHERE EXISTS
  (SELECT 1 FROM budget_totals WHERE job_id=NEW.job_id
    AND spent_micros+reserved_micros+NEW.bound_micros>limit_micros);
END;
CREATE TRIGGER trg_reservation_identity BEFORE UPDATE ON budget_reservations
WHEN NEW.reservation_id<>OLD.reservation_id OR NEW.job_id<>OLD.job_id
 OR NEW.operation_key<>OLD.operation_key OR NEW.bound_micros<>OLD.bound_micros
 OR NEW.bound_kind<>OLD.bound_kind OR NEW.bound_basis<>OLD.bound_basis
BEGIN SELECT RAISE(ABORT,'immutable reservation identity'); END;
CREATE TRIGGER trg_reservation_terminal BEFORE UPDATE ON budget_reservations
WHEN OLD.state IN ('settled','released')
BEGIN SELECT RAISE(ABORT,'reservation already terminal'); END;
CREATE TRIGGER trg_reservation_no_delete BEFORE DELETE ON budget_reservations
BEGIN SELECT RAISE(ABORT,'append-only accounting history'); END;
-- 重复相同 settlement_key 由服务端读回原结果；不得再次 UPDATE 或扣减聚合数。

CREATE TABLE leases (
  resource_id TEXT PRIMARY KEY,          -- 'desktop:local-01'
  holder_kind TEXT NOT NULL CHECK (holder_kind IN ('attempt','gateway','none')), holder_id TEXT,
  epoch INTEGER NOT NULL,                -- fencing 纪元，单调递增
  state TEXT NOT NULL CHECK (state IN ('free','granted','revoking','revoked','quarantined')),
  granted_at TEXT, heartbeat_at TEXT, expires_at TEXT,
  CHECK (state<>'free' OR holder_id IS NULL));

CREATE TABLE events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, job_id TEXT,
  type TEXT NOT NULL, entity_type TEXT, entity_id TEXT,
  actor TEXT NOT NULL, payload TEXT NOT NULL,    -- JSON，已脱敏
  created_at TEXT NOT NULL);
```

## 3. 状态机

### 3.1 任务

```
pending ─► ready ─► running ─┬─► succeeded
   ▲          ▲  │            ├─► failed ──(人工/规则)──► ready
   │          │  └─ retry_wait ◄┘（可重试且 attempt_count<max）
   │          │                ├─► needs_input
   └─ 依赖解除 ┘                └─► cancelled（终态）
budget_blocked：预留失败，不消耗 attempt
stale：revision 变化或上游失效，只能被新 revision 任务取代，不能提交
```

| 转换 | 条件 | 副作用 |
| --- | --- | --- |
| pending→ready | artifact 依赖均 succeeded 且 `to_task.revision == job.current_revision` | 写 `inputs_digest` |
| ready→running | 取得 attempt（唯一索引）+ 预算预留 + 若需桌面则已在 `granted` 租约下 | 写 `attempt.started` |
| running→succeeded | 服务端重算 `outputs_digest` 一致，产物全部落库 | 仅释放未发送的预留；UNKNOWN 保留，解除下游 |
| running→retry_wait | `retryable=true` 且 attempts<max | 指数退避 `not_before` |
| 任意→stale | `current_revision` 增加或上游 invalidated | 拒绝后续提交 |

### 3.2 发布意图

```
draft → awaiting_human → approved → submitting ┬→ accepted → reviewing → published
   │                                            ├→ unknown → reconcile → published|rejected|failed
   └─(拒绝/超时)→ cancelled                      └→ rejected / failed
```

`approved` 仅在存在未使用、未撤销、未过期且绑定匹配的 `human_approvals` 时可达；`external.submit` 在领取发送权的同一事务置 `used_at`。人类审批接口使用独立管理 UID/受信任登录通道；Agent 所在用户无该通道凭证、socket 权限或自行签发能力。PTY/TTY、输入 intent ID 或客户端自报角色都不是身份认证。协调器根据服务端会话身份验证批准者，并绑定完整发布包哈希、MP4、平台、账号、时间、revision；字段改变必须新建包版本并重新审批。`unknown` 是等待态而非失败，**禁止**从 `unknown` 直接回 `submitting`。

## 4. 桌面租约：fencing 的实际机制

TTL 只是"怀疑"，不是"释放"。以下序列是强制点。

### 4.1 授予

```text
lease.acquire(resource, attempt_id, session_id)
 1 由协调器串行处理该资源；服务端核验角色、task/revision 与能力范围
 2 若同 attempt 已 granted，只做幂等查询/续约，不另增 epoch
 3 只有数据库与 gateway 都确认 free、无旧在途动作，才进入 grant_pending
   （实现可用 leases.revoking 加 checkpoint 记录此过渡，不能提前交付 ticket）
 4 gateway.prepare：单调递增 epoch 并 fsync；绑定新会话但暂不接受控制
 5 SQLite 事务写 granted/epoch/holder 与事件；commit 后 gateway.activate
 6 确认激活后才交付 ticket。任一步响应未知：冻结资源并按两端状态对账，不授新持有者
```

`[决定]` 网关 epoch 持久化高水位不可回退；协调器同时检查数据库、网关与恢复记录。任一端回滚或缺失均进入隔离恢复，而非重置为 1。每次网关重启先封禁旧会话并完成 §4.3 的在途动作清理。外部调用不得在 SQLite 写事务中无限等待，使用有界 RPC 和明确过渡状态。

### 4.2 网关侧校验（真正拦住的地方）

```python
# 伪代码，契约示意
def handle_control(req):
    if resource_state != "active":        return reject("FENCED")
    sess = sessions.get(req.attempt_id)
    if sess is None:                       return reject("SESSION_DEAD")
    if req.epoch != current_epoch:         return reject("FENCED")     # 旧 epoch 一律拒
    if not mac_ok(req.ticket_mac, sess.k): return reject("BAD_TICKET")
    if sess.suspended_since:               return reject("SUSPENDED")  # SIGSTOP 过的会话
    if not task_scope_allows(req):         return reject("UNAUTHORIZED_CAPABILITY")
    return enqueue_with_epoch(req)  # 实际 dispatch 前再验 epoch；撤销会取消旧队列
```

每个输入事件、录屏控制、CDP 调用在接受与执行时都校验，队列项携带 epoch。网关只开放任务所需窗口/动作/目的地，不开放任意脚本执行或直接返回 CDP/OBS 凭据。高影响提交仍需对应审批，能力令牌不能授权任务外的发布、付款或账户修改。隔离必须由 OS 与受限工具接入证明；操作员仍可能被语义注入误导，所以不能声称“注入只能产生错误文字”。

### 4.3 回收：失联必须真正失效

触发：心跳超过 `heartbeat_deadline`（3×间隔且 ≥90s），或人类 `avc fence`。

```text
1 封禁  gateway 先原子标记 revoking，递增 epoch 并 fsync；拒绝新/旧 ticket，取消未执行队列
2 隔离  SQLite 记录 revoking 与事件；中途崩溃保持隔离，不自动改 free
3 排空  取消或等待所有已分派的键鼠/CDP/OBS 动作结束；未知动作结果先查询/人工核实
4 终止  由受信任 supervisor 终止旧进程组/容器，并获得 waitpid/容器终态等正面证据
        仅超时、无响应、挑战失败不能证明死亡；无法确认终止或在途动作静默则 quarantined
5 封存  停止并核实录制状态；原文件保留，能解码则登记，不能解码则隔离为损坏产物
6 对账  确认旧请求全部被拒、无在途输入、进程已终止、录屏已停止且文件归属清楚
7 放行  SQLite 写 free/holder=NULL 与事件，gateway 同步 free；下一次授予使用新 epoch
```

静默探测仅提供辅助证据，不得通过在桌面执行随机写入动作“证明死亡”。无法修复的录制文件不需要永远占住桌面，但必须隔离且不能被算作成功产物。重启、DB 回滚、SIGSTOP、取消均走同一撤销协议。若执行环境不能提供独占权限或可验证终止，阻断无人值守桌面任务，只允许明确的人类监督替代流程，不宣称 fencing 已通过。

### 4.4 心跳与时钟

执行者每 30s 上报，服务端依据 monotonic 时间更新本次进程的租约截止值。TTL 到期仅触发撤销，不代表释放。恢复后旧 UTC 截止值只能用于审计和保守隔离，不能作为旧会话已死的证明。

## 5. 原子产物与数据库一致性

```text
artifact.commit(attempt, entries, idempotency_key)
 1 验证 attempt/revision/capability，取得提交与 GC 互斥锁
 2 以受控目录 fd 解析相对路径；拒绝绝对路径、..、软链接、设备文件和越界硬链接
 3 从已打开的普通文件 fd 复制到协调器独占的 store/tmp/<uuid>
   在复制后的私有文件上计算 hash、媒体校验与长度；源文件仍可写也不影响私有快照
 4 再次校验 attempt 未被取消/fenced/revised；私有文件 fsync
 5 同挂载点原子发布到 objects/<sha>；权限/所有者保证 Agent 不能写对象或打开可写句柄
   目标已存在必须验证其内容 hash，不仅比较长度；fsync 目标目录及受影响目录
 6 SQLite 同一事务插入 assets、artifact_refs、outputs_digest 和事件；提交幂等记录
 7 返回不可变引用。中途拒绝仅产生隔离 staging/孤儿对象，不给成功收据
```

- 文件先于 DB；DB 提交前出现孤儿对象可 GC，DB 不得指向半写文件。公开对象不可原地修改。
- `store/tmp`、`store/objects` 同挂载点；源 attempt 可位于别的挂载点，因为使用受控复制而非跨文件系统 rename。
- 协调器私有 staging 与对象库不可由 Agent 列出/写入；通过只读产物服务或受控只读挂载消费。保留源文件以便审计，不将可写 inode 直接 rename 成可信对象。
- 请求幂等键关联规范化 entry 清单和返回结果；同键不同清单拒绝。终态不再接受新内容提交。

### 5.1 垃圾回收

| 对象 | 判定 | 处理 |
| --- | --- | --- |
| 有 `artifact_refs`/`segments`/`video_versions` 引用 | 正常 | 永不删 |
| 零引用且 >7 天 | 孤儿 | 移 `trash/<date>/`，7 天后删 |
| `store/tmp` 残留 | 无 DB 行 | 24h 后移 `quarantine` |
| superseded 但仍被旧 `publications` 引用 | 历史 | 保留 |
| `attempt-*` 临时目录 | 终态后 14 天 | 归档或删 |

GC 与提交共用 `gc` 锁，且扫描所有数据库外键、发布包及 manifest 的递归引用闭包。未知引用或备份保留窗口中的对象不可删除；删除前生成 dry-run 清单，正常运行中不执行破坏性验收。

## 6. 版本变更与下游失效

### 6.1 影响传播（触发器实现，不是文档提醒）

```sql
-- 实测结果变差 → 冻结文案失效
CREATE TRIGGER trg_test_change AFTER UPDATE ON test_runs
WHEN NEW.result IS NOT OLD.result AND NEW.result <> 'success' BEGIN
  UPDATE script_versions SET state='invalidated' WHERE job_id=NEW.job_id AND state='frozen'
   AND script_version_id IN (SELECT script_version_id FROM script_test_basis
                             WHERE test_run_id=NEW.test_run_id); END;

-- 证据被隔离 → claim 失去支撑
CREATE TRIGGER trg_ev_quarantine AFTER UPDATE OF quarantined ON evidence
WHEN NEW.quarantined=1 AND OLD.quarantined=0 BEGIN
  UPDATE claims SET status='unsupported' WHERE claim_id IN
   (SELECT claim_id FROM claim_evidence WHERE evidence_id=NEW.evidence_id); END;

-- claim 失支撑 → 依赖它的冻结文案失效
CREATE TRIGGER trg_claim_unsupported AFTER UPDATE OF status ON claims
WHEN NEW.status='unsupported' BEGIN
  UPDATE script_versions SET state='invalidated' WHERE state='frozen'
   AND script_version_id IN (SELECT sv.script_version_id FROM script_versions sv
     JOIN script_lines l ON l.script_version_id=sv.script_version_id
     JOIN line_claims lc ON lc.line_id=l.line_id WHERE lc.claim_id=NEW.claim_id); END;

-- 文案失效 → 剪辑单失效 → 成片失效 → 审批失效（同一事务内级联）
CREATE TRIGGER trg_script_invalidated AFTER UPDATE OF state ON script_versions
WHEN NEW.state='invalidated' BEGIN
  UPDATE edit_plans SET state='invalidated' WHERE script_version_id=NEW.script_version_id; END;

CREATE TRIGGER trg_plan_invalidated AFTER UPDATE OF state ON edit_plans
WHEN NEW.state='invalidated' BEGIN
  UPDATE video_versions SET state='invalidated', invalid_reason='upstream edit_plan invalidated'
   WHERE edit_plan_id=NEW.edit_plan_id; END;

CREATE TRIGGER trg_video_invalidated AFTER UPDATE OF state ON video_versions
WHEN NEW.state='invalidated' BEGIN
  UPDATE qa_approvals SET state='invalidated', invalid_reason='upstream video_version invalidated'
   WHERE video_version_id=NEW.video_version_id AND state='valid';
  UPDATE publish_intents SET state='cancelled'
   WHERE video_version_id=NEW.video_version_id
     AND state IN ('draft','awaiting_human','approved'); END;
```


| 变化 | 失效对象 | 后果 |
| --- | --- | --- |
| `test_run` 结果变差 | `script_versions(frozen)` | 文案需重定稿 |
| evidence 隔离 | `claims` | 相关行改写或删除 |
| claim unsupported | `script_versions` | 见下 |
| script invalidated | `edit_plans` → `video_versions` → `qa_approvals` | 审批 `invalidated`；未提交的 intent → `cancelled` |
| `edit_plan` 变更 | 对应 `video_versions(invalidated)` | 必须重渲、重 QA |
| 模板/渲染器版本变更 | 新 `edit_plan` 版本 | 历史成片不被静默替换 |
| `plan_revision` 增加 | 旧 revision 的未完成任务（含 running） | → `stale`；撤销旧 attempt 能力与桌面租约，拒绝迟到提交；已提交产出保留 |

`[决定]` **已发布作品不回溯失效**：`publications` 保留，另有 `requires_correction` 标记供人工决定下架或更正，系统不自动改动已发布内容。

### 6.2 版本与幂等

- 文案、剪辑单与成片采用不可变版本；创建新版本时带当前版本引用，服务端 CAS 检查。任务/租约用当前状态和事件序号检查，不依赖不存在的通用 version 列。
- `sha256` 判定"同一产物"；`video_versions.version` 判定"同一条片的一次尝试"。只有输出字节相同才复用 `mp4_asset_id`。重建验收默认采用媒体/画面/音轨等价标准，固定编码环境的 bit-exact 检查可另外执行；即使内容相同仍新增 version 行保留过程。

## 7. 血缘不变量

```sql
-- 片段若挂 claim，其源素材必须是对应 claim 的支持证据
CREATE TRIGGER trg_seg_claim BEFORE INSERT ON segments
WHEN NEW.claim_id IS NOT NULL AND NEW.source_asset_id IS NOT NULL BEGIN
  SELECT RAISE(ABORT,'claim source asset not linked') WHERE NOT EXISTS (
    SELECT 1 FROM claim_evidence ce JOIN evidence e ON e.evidence_id=ce.evidence_id
     WHERE ce.claim_id=NEW.claim_id AND ce.relation='supports'
       AND e.asset_id=NEW.source_asset_id AND e.quarantined=0); END;
```

冻结准入（`stage.complete` 内执行，SQL 化便于审计）：

```sql
SELECT (SELECT count(*) FROM script_lines l
          JOIN line_claims lc ON lc.line_id=l.line_id
          JOIN claims c ON c.claim_id=lc.claim_id
         WHERE l.script_version_id=:sv AND c.requires_evidence=1 AND c.status<>'supported') AS unsupported,
       (SELECT count(*) FROM script_test_basis b JOIN test_runs t ON t.test_run_id=b.test_run_id
         WHERE b.script_version_id=:sv AND t.result='success' AND t.ended_at IS NOT NULL) AS bases;
-- unsupported>0 或 bases=0 → 拒绝冻结（INVALID_TRANSITION）
```

`[决定]` "实测完成后才允许文案定稿"是三重合取：存在 succeeded `test_run`；所有必要 claim 有同job未隔离的支持证据；实测已结束且 `test_run.ended_at <= script.frozen_at`。只有数据库完整性不足以判断证据语义是否真正支持文案，仍需独立审查。

### 7.1 冻结守卫示例

冻结事务重新核验输入、同job关联和当前证据，不只相信 `claims.status`；草稿无claim的事实句由内容审查发现，不能以数据库检查代替语义判断。冻结后的行和关联不可原地修改，编辑必须新建script版本；协调器先复制再修改，并重新走守卫。

```sql
CREATE TRIGGER trg_script_starts_draft BEFORE INSERT ON script_versions
WHEN NEW.state<>'draft'
BEGIN SELECT RAISE(ABORT,'script must start draft'); END;
CREATE TRIGGER trg_script_freeze BEFORE UPDATE OF state ON script_versions
WHEN NEW.state='frozen' BEGIN
 SELECT RAISE(ABORT,'freeze requires completed successful test') WHERE
  NEW.frozen_at IS NULL OR NOT EXISTS (
   SELECT 1 FROM script_test_basis b JOIN test_runs t ON t.test_run_id=b.test_run_id
   WHERE b.script_version_id=NEW.script_version_id AND t.job_id=NEW.job_id
    AND t.result='success' AND t.ended_at IS NOT NULL AND t.ended_at<=NEW.frozen_at);
 SELECT RAISE(ABORT,'unsupported or cross-job claim') WHERE EXISTS (
  SELECT 1 FROM script_lines l JOIN line_claims lc ON lc.line_id=l.line_id
   JOIN claims c ON c.claim_id=lc.claim_id
  WHERE l.script_version_id=NEW.script_version_id AND
   (c.job_id<>NEW.job_id OR (c.requires_evidence=1 AND
    (c.status<>'supported' OR NOT EXISTS (
     SELECT 1 FROM claim_evidence ce JOIN evidence e ON e.evidence_id=ce.evidence_id
      JOIN test_runs t ON t.test_run_id=e.test_run_id
     WHERE ce.claim_id=c.claim_id AND ce.relation='supports' AND e.quarantined=0
      AND t.job_id=NEW.job_id AND t.result='success' AND t.ended_at IS NOT NULL
      AND t.ended_at<=NEW.frozen_at)))));
END;
CREATE TRIGGER trg_basis_same_job BEFORE INSERT ON script_test_basis
WHEN (SELECT job_id FROM script_versions WHERE script_version_id=NEW.script_version_id)
 IS NOT (SELECT job_id FROM test_runs WHERE test_run_id=NEW.test_run_id)
BEGIN SELECT RAISE(ABORT,'cross-job test basis'); END;
```

`segments` 插入和修改使用同一服务端校验器；已冻结版本的任何修改、删除关系及许可变更均应失效下游或被拒绝。上面的 INSERT 触发器不是“修改也自动安全”的证明，发布前仍在同一事务复核完整血缘、许可、包哈希与审批。实现阶段须补全 UPDATE/DELETE 不变量测试。

## 8. 外部提交：UNKNOWN 与对账

### 8.1 原子领取发送权

发布包按规范化 JSON 哈希，包含视频、封面、标题、简介、标签、声明、素材许可、平台、账号、计划时间与 revision。准备后不原地修改；新内容生成新包并重新审批。

```text
external.submit(op_id, payload)
 1 BEGIN IMMEDIATE：验证当前任务/QA/完整包/人类身份审批/预算 reservation
   幂等插入 PREPARED（同 key 必须同 request_digest）；已有提交状态只读回，不重发
 2 同事务 CAS：PREPARED→SUBMITTING，设置唯一 send_claim_id，并消费尚有效审批
   CAS影响行必须=1；否则不得发请求。状态和事件先提交并持久化
 3 只向本次 CAS 成功的发送者交付一次内存发送许可；可选 journal 是DB镜像，不是第二权威
 4 发送一次；禁客户端自动重试/重定向导致重复副作用
 5 成功有回执→ACCEPTED/RETURNED；有明确未受理依据→FAILED；其余→UNKNOWN
```

提交状态已落库但尚未发送就崩溃，也按 UNKNOWN 保守处理，不能从磁盘重放 send_claim_id。MVP 人工交接只控制一次交接许可，无法约束人在系统之外手动重复上传。普通只读查询可按限额重试，不得与有副作用提交混用策略。

```sql
CREATE TRIGGER trg_no_resubmit BEFORE UPDATE OF state ON external_operations
WHEN OLD.state<>'PREPARED' AND NEW.state IN ('PREPARED','SUBMITTING')
BEGIN SELECT RAISE(ABORT,'send claim cannot be replayed'); END;
CREATE TRIGGER trg_submit_claim BEFORE UPDATE OF state ON external_operations
WHEN NEW.state='SUBMITTING' AND NEW.send_claim_id IS NULL
BEGIN SELECT RAISE(ABORT,'send claim required'); END;
CREATE TRIGGER trg_request_immutable BEFORE UPDATE ON external_operations
WHEN NEW.provider<>OLD.provider OR NEW.idempotency_key<>OLD.idempotency_key
 OR NEW.request_digest<>OLD.request_digest OR NEW.intent_id IS NOT OLD.intent_id
 OR (OLD.send_claim_id IS NOT NULL AND NEW.send_claim_id IS NOT OLD.send_claim_id)
BEGIN SELECT RAISE(ABORT,'immutable external request'); END;
CREATE TRIGGER trg_approved_package_immutable BEFORE UPDATE ON publish_intents
WHEN OLD.state NOT IN ('draft','awaiting_human') AND
 (NEW.package_asset_id<>OLD.package_asset_id OR NEW.package_sha256<>OLD.package_sha256
  OR NEW.platform<>OLD.platform OR NEW.account_ref<>OLD.account_ref
  OR NEW.video_version_id<>OLD.video_version_id OR NEW.scheduled_at IS NOT OLD.scheduled_at
  OR NEW.plan_revision<>OLD.plan_revision OR NEW.dedupe_key<>OLD.dedupe_key)
BEGIN SELECT RAISE(ABORT,'approved package cannot change'); END;
```

审批状态匹配与一次消费在协调器同一事务内执行，使用 `used_at IS NULL AND revoked_at IS NULL` 的 CAS；不能依赖上述触发器自动验证人类身份。管理端签名/身份凭据永不交给 Agent，Agent 只能获得已批准 intent 的不透明引用。审批失效、过期、包变化和跨账号复用均拒绝。

### 8.2 对账及有条件的新尝试

- 查到对应外部对象：记录证据、外部 ID 和最终状态；仅凭上传成功不能写公开发布。
- 没查到不等于不存在：普通 404、列表暂缺、超时或截图均不足以自动重提。需要供应商明确终止/未受理证明，查询覆盖对应请求与账号，且超过其文档给出的可见性窗口；无此能力则人工核实并保持 UNKNOWN。
- 有权威否定证据时，旧操作置 RECONCILED，保存证据哈希。人类重新批准后，可创建最多一次后继操作，`retry_of` 指向旧 op，使用新的发送 key；同一 intent 的 successor 链深度服务端限制为 1。不自动递归创建后继。
- 查询失败保留 UNKNOWN；只读查询最多 6 次/24h，之后进入 needs_input。人工结论由独立审批身份提交，不能由 Agent 自报人类身份。
- 人工上传后未回填时只能对账，不能自动补发。取消/重启不清除 dedupe 与 UNKNOWN 记录。

### 8.3 可承诺的范围

本地同一逻辑发布计划只有一个 intent；每个外部操作的发送许可至多授予一次，UNKNOWN 期间零重提。这不是跨重试或跨平台的严格 at-most-once / exactly-once：显式获准的新尝试和外部重复仍可能发生，平台不提供幂等能力时不能保证“作品绝不重复”。记录每个实际尝试与回执，不用新 op 绕过未完成对账。

### 8.4 状态不混用

内部草稿、平台草稿、私密作品、审核中、公开、已删除分别记录；`publish_intents.state` 与 `publications.final_state` 不互相替代。

## 9. 取消与重试契约

```text
task.cancel(task_id, reason)
 无 running attempt → cancelled（终态）
 否则：1 打 cancel_requested 标记 → 2 avc 返回 CANCEL_REQUESTED
       3 若需桌面 → 走 §4.3 全流程（不能只发消息）
       4 宽限 60s，读 checkpoint → 5 超时强制终止，attempt=orphaned，产物按 truncated
       6 预算：只释放已证明未发送的操作；UNKNOWN继续预留，已知费用幂等结算
```

- 取消是终态，不自动重启；重开需新任务。
- 已发起的 `external_operations` **不撤销**，转 UNKNOWN 对账（外部可能已生效）。
- 已被引用的产物不删除，保持可审计。

| 错误类别 | 例子 | 策略 |
| --- | --- | --- |
| `transient` | 网络抖动、429、OBS 查询后确认未开始 | 退避重试，≤max_attempts |
| `deterministic` | 哈希不符、剪辑单校验失败、模板缺失 | 不重试，`failed` |
| `environment` | 登录过期、验证码、页面改版 | `needs_input`，保存现场 |
| `external_unknown` | 提交超时 | **禁止重试**，必须对账 |
| `resource_conflict` | 租约被占 | `retry_wait`，不消耗 attempt |

`[决定]` 每次重试生成新 attempt 与新目录；`inputs_digest` 变化时必须开新 task 版本，而非重试。

## 10. 关键样例

```json
{"task_id":"video-001/capture/shot-03","attempt_id":"at_01J9ZQ5C8K",
 "lease":{"resource_id":"desktop:local-01","epoch":12,"gateway_ticket":"mac:...",
          "expires_at":"2026-11-02T09:44:07.000Z"},
 "inputs_digest":"sha256:6f1c...",
 "deliverables":[{"kind":"raw_video","sha256":"9a2b...","rel_path":"objects/9a/9a2b...",
   "media":{"duration_ms":48200,"w":1920,"h":1080,"fps":30,"audio_codec":"aac"}}]}
```

```json
{"op_id":"op_01J9ZQ7F2M","provider":"douyin_api","operation":"create",
 "idempotency_key":"video-001|v3|douyin|acct-main|2026-11-03T12:00Z",
 "request_digest":"sha256:aa91...","state":"UNKNOWN",
 "submitted_at":"2026-11-02T10:01:12.400Z","reconcile_attempts":1,"next_action":"reconcile"}
```

## 11. 预算：操作级预留、幂等结算与取消

- **hard** 模式只准入具有可执行费用上界的操作：固定价格/模型最大输出、输入和缓存上界、循环次数、媒体时长、供应商实际可强制额度等。预留 `enforced_max`，不是 p50；无法证实上界则停在 needs_input，不得伪称硬限额。
- **soft** 模式经人类选择后可按估算准入；实际可能超出估计，明确只提供预测、停止后续调用和告警，不承诺无超支。费用按供应商账单对账，模型标价不冒充实际费用。
- 每次有费用操作先以唯一 `(job_id, operation_key)` 创建 reservation；所有准入在 `BEGIN IMMEDIATE` 内从 `budget_totals` 读余额。并发操作不能共享一个可重复释放的 stage 估算。
- 每次结算以 `settlement_key` 幂等：同键同结果读回；同键不同金额拒绝。只有 held/unknown 能 CAS 到 settled 或有明确未发送证据时到 released。终态不可修改；修正账单另记带人类依据的审计调整，不静默覆盖历史。
- 费用未知保持 unknown 和原预留；若可信价格变化使最大暴露增加，先停止新的有费动作并请求人工处理，不能释放旧预留后假装有余额。
- 已发生的实际超额不能拒绝入账以“满足CHECK”：如供应商违反上界，仍真实记账并触发 overrun 事件、阻断新操作。硬上限保证依赖其明确列出的外部计价假设，验收必须检查这些条件。
- 预算聚合由 reservation 视图计算，不使用仅靠 `reserved>=0` 的重复释放防护。limit、mode 的修改只允许独立管理身份，并写事件。

```text
reserve(operation_key, bound, basis)
 BEGIN IMMEDIATE；校验当前任务、mode和bound依据；插入held（触发器检查余额）；写事件；COMMIT
send()
 只有reservation已持久化且范围匹配，才能领取外部发送权
settle(reservation_id, settlement_key, actual)
 BEGIN IMMEDIATE；若同key已结算则读回；否则CAS held/unknown→settled；写事件；COMMIT
cancel()
 未发送有证据→released；可能已发送→unknown；实际已结算→保持，不再次扣减/释放
```

## 12. edit_plan v1 与音频契约

首版限定一个模板与 FFmpeg。下例用逻辑 asset ID，提交时必须解析为已有不可变资产及其真实哈希；示例中的 ID 不是已生成素材。schema_version 固定，未知必需字段/后端拒绝。所有时间轴用整数帧，30fps、半开区间 `[in,out)`；音频来源采样以48000Hz PCM对齐，禁止把浮点秒作为主键。

```json
{
  "schema_version": 1,
  "plan_id": "edit-demo-v1",
  "script_version_id": "script-demo-v1",
  "template": {"id": "vertical-demo-v1", "font_asset_id": "font-cleared"},
  "output": {"width": 1080, "height": 1920, "fps_num": 30, "fps_den": 1, "frames": 1800, "audio_hz": 48000},
  "video": [{"asset_id": "capture-01", "source_in_frame": 0, "source_out_frame": 1800,
    "timeline_in_frame": 0, "timeline_out_frame": 1800,
    "crop_px": [0, 0, 1920, 1080], "fit": "contain", "target_px": [0, 420, 1080, 608],
    "evidence_ids": ["evidence-01"]}],
  "audio": [{"asset_id": "voice-01", "role": "narration", "timeline_in_frame": 0,
    "timeline_out_frame": 1800, "gain_db": 0, "fade_in_frames": 3, "fade_out_frames": 3}],
  "subtitles": [{"line_id": "line-01", "in_frame": 0, "out_frame": 90,
    "text": "示例文案，须与已冻结文案一致", "safe_area_px": [60, 1100, 960, 600]}],
  "mix": {"target_lufs": -16, "true_peak_dbtp_max": -1},
  "expected_silent_intervals": [],
  "reproducibility": {"renderer_build_ref": "approved-build", "mode": "media-equivalent"}
}
```

校验器必须检查：所有片段在 `[0,frames)`，同一轨道无意外重叠/缺口；source区间未越界，裁切框在素材尺寸内，缩放策略明确；帧率转换由固定renderer参数执行；文字来自冻结script，旁白/字幕逐句对齐；字体、音频、视频都有当前用途适用的许可；输出尺寸、帧率、音轨与模板一致。透明层可重叠，字幕间隙必须与实际语音停顿一致，不能用“无间隙”强迫全程显示字幕。长等待剪短须保留真实耗时说明。混音参数是首版假设，通过真峰值、响度和人工看听校准。

**音频 MVP**：先由人工提供有权使用的旁白 WAV/音频文件，登记来源/许可、哈希、采样率、声道、实际时长和逐句时间点；音频变更创建新script/edit版本并重做QA。默认不依赖未选定的TTS供应商。

**可选 TTS 适配器契约**：输入 `script_version_id + text_digest + language + voice_ref + format + operation_key + budget_reservation_id`；输出不可变音频asset、实际时长、句子对齐（缺失则人工对齐）、外部request ID、usage与许可依据。没有已批准的供应商/声音授权不发送；请求含文本而非无关项目文件；未知生成结果按 §8 对账，不能直接重生成。真人声克隆另需明确授权，不由本方案默认启用。BGM独立登记，混音不得遮盖旁白。

## 13. 备份与恢复范围

恢复快照包括：SQLite一致性备份（含事件、审批、UNKNOWN和dedupe历史）、对象引用闭包、模板/渲染器版本、必要journal与配置版本引用。凭证独立安全备份，不放版本仓库。使用SQLite备份API或停写检查点方式，不在活跃WAL时只复制主db文件。备份完成核验哈希和可读性后才标记可恢复。

在独立临时恢复目录演练，不删除生产 `var/`。仅有媒体objects不能恢复发布事实；缺失数据库或journal时发布任务进入 needs_input，禁止推断“未发布”后重传。恢复后的gateway先撤销旧会话再授新epoch，备份不能恢复正在使用的旧ticket。重建视频验收只删除临时恢复目录内的派生渲染输出，保留原片、对象、模板、数据和清单。
