# 实测执行Agent：Claude初稿与Codex review

2026-11-04。此模块与并行剪辑Agent隔离，不改共享入口或编辑模块。

真实Claude Code 2.1.310 (Claude Code)，既有muskapis，safe-mode/无工具/空MCP/无hooks，52.987秒退出0，供应商报告模型`claude-opus-5-5[1m]`。仅发送获准项目摘要，不发送媒体/私人资料或凭据。初稿SHA256：`3c54b0102b3d996d0976c4f8da390f5ffa7be2e223c99489b3d09da7640effad`。

## Claude原文

# auto-video · execution_agent 窄版设计

## 1. 边界与目录
新增独立包 `execution_agent/`、独立 CLI、Skill、docs、tests；不改剪辑模块、README、共享入口。运行根为私有目录 `runs/<test_run_id>/`（0700），源 fixture 只读、cwd 用新空目录。本轮不碰桌面/录屏，不做网关。

## 2. 身份与冻结
`request_id`（调用方便捷键：幂等键）、`test_run_id`（ULID）、`attempt_no`、`step_id`、`operation_id`（与 request 绑定，一经创建不可变）。执行前先写 `intent.json` 并 fsync：`script_sha256`、`input_sha256`、`tool_descriptor_id+version_hash`、argv 数组、env 白名单、声明产物。**意图持久化成功之后才允许副作用**。重试只能换 `attempt_no`、复用同 `operation_id`；unknown 不得改 key 重跑。

## 3. 状态机
- run：`planned → running → completed | completed_with_gaps | aborted`
- step：`pending → ready → running → succeeded | failed | blocked | not_executed | unknown`
- operation：同上，且 `unknown` 是**执行层独立状态**，另带 `process_ok` 与 `result_verified` 两个互不推导的布尔。

unknown 只进对账队列，禁止同 key 重放、禁止改新 key 绕过。对账后由映射层落到既有 SQL 枚举 `success/partial/failure/blocked`（`succeeded=success`）；未对账前 SQL 侧一律按 `blocked` 处理，**不新增字段、不扩枚举**。run 级结果取最保守合成：存在未对账 unknown 则不得为 success。

## 4. 输出契约（单行 JSON）
`schema_version`、五类 id、`status`、`times{started_mono_ns,ended_mono_ns,elapsed_ms,observation_wait_ms}`、`operations[]{operation_id,step_id,adapter,argv_hash,attempt_no,status,exit_code,process_ok,result_verified,error{code,message,source},limits[]{name,value,provenance},evidence[]{kind,path,sha256,bytes,truncated}}`、`claims[]{claim_id,statement,evidence_refs,confidence,synthetic}`、`unverifiable[]`。规则：exit_code=0 不推出 verified；证据 sha256 必填；超长输出截断记 `truncated=true` 并保留首尾哈希；费用/限制只写有来源值，缺来源记 `unknown` + provenance。

## 5. 命令（argv 形式，禁 shell 拼接）
```
python3.12 -m execution_agent plan  --manifest M --out RUN
python3.12 -m execution_agent run   --run-dir RUN --adapter file_transform --input P --request-id ID
python3.12 -m execution_agent status --run-dir RUN
python3.12 -m execution_agent observe --run-dir RUN --operation OP
python3.12 -m execution_agent reconcile --run-dir RUN --source fs|recording_agent
python3.12 -m execution_agent handoff --run-dir RUN --for screenwriter --out H
```
stdout 仅单行契约 JSON，人类可读日志走 stderr；退出码只表示 CLI 是否完成，不表示业务成功。

## 6. 最小 adapter：file_transform
仓库内确定性变换：读只读 fixture，按固定规则生成新文件，写入用 `O_EXCL`，目标已存在即失败；拒绝 symlink 与指向私有目录外的硬链接；记录 `processed_bytes`、`max_bytes` 上限。capability 清单仅"本地文件读写"，清单外一律 `blocked`。fixture 只证明该 adapter 自身，**不声明支持任意软件**。

## 7. UI 监督桥接
CLI 不操作桌面。Skill 经受支持 CUA 执行 UI 步骤，把控件路径、截图 sha256、观察等待、耗时以回调回填同一 `operation_id`；CLI 只接受带证据的回调，缺证据即 `result_verified=false → unknown`。不从 CLI 绕过 CUA、不注入第三方页面。录屏 take 引用**独立** `test_run_id`（多对一，非 1:1）；manifest 中的 `upstream_test_run_id` 未经验证时，handoff 标 `unverified`。

## 8. 对编剧 handoff
只输出有证据的 claim 与显式 `unverifiable[]`（"不能说的结论"清单）；`synthetic=true` 的结果永不作为实测事实；缺证据不得定稿。

## 9. 审查点（Codex review）
意图先于副作用的顺序；O_EXCL 与非覆盖；unknown 无重放路径；过程/结果分列在契约与 handoff 双处成立；时长取 monotonic 源且观察等待单列；私有路径/symlink/证据哈希/截断；无 shell 拼接；未误加 SQL 字段；未把 fixture 当通用能力。

## 10. 测试矩阵
1) 正常变换 verified=true；2) exit 0 但哈希不符→failure（证明进程成功≠正确）；3) 副作用前中断→not_executed，副作用后→unknown 且拒重放；4) 同 request_id 幂等；5) 同 step 并发被互斥拒绝；6) 目标存在→拒覆盖；7) symlink 输入→拒绝；8) 大输出截断且哈希稳定；9) UI 回调缺证据→unknown；10) 能力外请求→blocked 并记 limits；11) reconcile 仅做映射、不写正式库；12) 无证据 claim→handoff 拒绝出稿。

## 11. 范围限制
SQL `test_runs/claims/evidence` 与映射层均为**设计态，未部署**，不得声称已接入；不操作桌面/录屏、不登录、不付费、不外部提交；无独立批准或 reservation 不执行高影响操作；本轮仅 CLI 安全模拟与仓库内可控 fixture。

## Codex review及实施范围

- 未闭环unknown不写SQL候选result/succeeded；不以blocked掩盖可能已执行。正式数据库适配不存在。
- intent落盘后崩溃、即使看不到产物也保守unknown；只有明确未启动的错误可判未执行。无自动重试。
- MVP自动执行仅固定本地file_transform worker，不接受任意argv/shell/网络/登录/付费/外部提交。worker source hash随输入和脚本冻结；过程退出与产物验证分开。
- UI桥只发出监督操作意图并回填，CLI不控制电脑；CUA由Skill在已协调桌面时段操作。单靠操作员success说明或截图不通过结果验收；明确其断言来源与独立文件验证的边界。
- 同request重放不执行；同step不重复；同job/operation_key尚unknown时新run也阻断。协作文件锁会被子进程继承，不能以PID存在判成功或以TTL取得桌面控制。不是防恶意同UID的OS沙箱。
- 安全fixture结果标synthetic，不当作真实第三方产品能力。编剧收到完整成功/失败/限制/耗时/证据与不能说的结论；本helper不冻结文案、不自动发消息。
- 首版只有一个attempt；新的业务复测需旧操作终结后新test_run，不把换ID当未知重试。具体CLI/契约以实施文档为准。
- 本轮只做无桌面控制的本地安全场景；真实GUI操作和录屏需主任务安排时段。本机现有原片/账号均不访问。

后续 GUI 验证 review（2026-11-04）：主任务释放桌面后，用本机 TextEdit 原创文本做真实监督界面操作。补充 test_kind 元数据，GUI fixture 也保守标 synthetic；明确“一个结果文件与冻结预期不符”为失败，“没有可靠结果文件”为 unknown。这是 Codex 对已设计验证链的局部修订，未另调用 Claude 或改写其初稿。实证见 [GUI 验证](test-execution-agent-gui-validation.md)。
