# 实测执行 Agent MVP

设计由本机 Claude Code 实际生成，Codex review 后原生实施；[原始方案与 review](test-execution-agent-design.md)记录来源。新增独立 `execution_agent/`，不改录屏、剪辑模块或共享入口。Python 3.12+，仅标准库，不安装服务/依赖。初轮离线验证后，已在获协调的桌面时段完成 [TextEdit 真实 GUI 安全场景](test-execution-agent-gui-validation.md)，未启动录屏。

## 已实现与范围

- CLI 按冻结脚本依次执行固定本地文本变换 worker；记录真实子进程退出、单调时钟耗时、产物独立验证、失败/限制和证据。安全故障注入只用于 fixture。
- GUI 经项目 Skill `execute-tool-test` 监督操作，CLI 发出意图并接受有身份绑定的观察回填。CLI 不控制鼠标、浏览器或软件。已实际通过受支持 CUA 操作 TextEdit 原创样例并完成回填；不是无人值守通用 GUI 适配器。
- 独立输出编剧 handoff JSON，包含成功事实、失败、未执行/阻塞/unknown、实测耗时、限制来源、哈希和不能说的结论。不发送聊天消息，不调用编剧模型，不上传证据。
- 没有正式 SQL、外部账号/费用网关、自动语义验收或发布。`sql_candidate`只是与现有枚举对齐的候选，`sql_imported=false`；unknown 时候选为 null。

## 可运行示例

从仓库根执行，运行根必须在 Git 外且 0700。例子真实启动三个受限本地子进程，但不操作第三方产品：

```sh
execution_runtime=$(mktemp -d "${TMPDIR:-/tmp}/auto-video-test.XXXXXX")
python3.12 -m execution_agent plan --root "$execution_runtime" \
  --run-id demo --request-id demo-plan --manifest fixtures/execution-agent/mixed.json
python3.12 -m execution_agent run --root "$execution_runtime" \
  --run-id demo --request-id demo-run
python3.12 -m execution_agent status --root "$execution_runtime" --run-id demo
python3.12 -m execution_agent handoff --root "$execution_runtime" \
  --run-id demo --request-id demo-handoff
```

预期 `partial`：第一步正确；第二步实际 exit 0 但产物错误；第三步实际触及 adapter 的 1 字节输入限制。fixture 的“产品限制”不能推广到 Gamma。所有 CLI stdout 是单行 JSON；exit 0 表示命令处理完成，业务结果看 `result`，参数/路径/存储错误 exit 2。

## 脚本 schema_version 1

严格拒绝缺版本、布尔 true、未知版本/字段及隐式 legacy。示例见 [mixed.json](../fixtures/execution-agent/mixed.json)。

| 字段 | 契约 |
| --- | --- |
| schema_version | 整数 1 |
| job_id / tool_id / script_id | 1–64 个 ASCII 字母、数字、`-`、`_` |
| tool_version | 场景声明值；不当作独立查询结果 |
| input_file | manifest 相邻的单个文件名；UTF-8、最多 1 MiB、常规单链接文件 |
| input_rights | generated_fixture / user_supplied，声明不代替真实授权 |
| test_kind（可选） | controlled_fixture / product_test；省略保守视为 controlled_fixture；不是来源证明 |
| steps | 1–50 个；step_id 和 operation_key 各自唯一；至少一个 required=true |
| step.adapter | file_transform / supervised_cua；其他值明确 blocked |
| step.scope | local_file / local_ui / external_submit / account / payment |
| step.params | file_transform 固定参数；其他 adapter 只能空对象 |
| step.expect | `{kind: sha256, value: 64位小写哈希}` 或 `{kind: text_equals, value: UTF-8文本}` |
| step.instruction | supervised_cua 必填，最多 4096 UTF-8 字节 |

只有 `file_transform+local_file` 自动执行；`supervised_cua+local_ui` 停在等待监督回填。local_ui 仅表示已获准、不产生额外外部持久副作用的观察/预览等操作。需生成远端结果、登录、付款等动作本 MVP 直接 blocked：独立可信批准及预算 reservation 网关尚未实现，不能靠 manifest 或助手自行确认。

固定参数：`transform=uppercase|normalize_newlines`，`max_input_bytes` 整数 1–1048576，`timeout_seconds` 0.05–30（默认 5），`delay_ms` 整数 0–35000，`noise_bytes` 整数 0–262144，`fixture_fault=wrong_result|exit_nonzero`。最后三项用于可控故障场景，所有自动 adapter 结果强制 synthetic=true。任意 argv、shell、网络、动态代码、第三方插件均不支持。此约束是代码能力边界，不是 OS 沙箱。

GUI 受控示例也必须 synthetic=true，以 test_kind 显式标注；省略采用保守 fixture 分类。product_test 只是调用者声明，不能豁免独立来源/语义/成功判据审核。旧存储未带该元数据时 summary 标 legacy_unspecified，不重写历史原件或已缓存 receipt。

## 持久化、身份与恢复

运行根 0700，文件 0600，输入/脚本快照 0400。已存在的公开 root/内部条目、symlink、hardlink、Git 内输出拒绝，不擅自 chmod 用户目录。系统拥有的 `/tmp`、`/var` 路径别名允许；其余任务路径检查链接。模型为同 UID 诚实协作者，不能抵御同 UID 恶意进程的 TOCTOU 或读取。

SQLite schema_version 1、WAL、synchronous=FULL 保存 run/step/request/operation；事务提交执行意图之后才能启动 worker。证据先写临时文件、fsync、硬链接无覆盖发布、fsync 目录；固定输出 O_EXCL，绝不覆盖既有产物。SQLite 和文件系统不是跨介质事务：崩溃遗留的 partial 目录/孤儿文件保留，不自动删除或改写。

冻结：canonical script SHA256、输入字节 SHA256、实际 worker 源码 SHA256、Python/OS/CPU 版本；自动执行前重新核对快照和 worker。worker 配置及 argv 的哈希入 step。工具声明版本来源保留；真实 bundled worker descriptor 包含源码哈希和其解释器版本，不冒充查询过第三方产品版本。

一次 test_run 可有多步，每步一个 operation、attempt_no=1。一个测试可拍多 take，一 take 也可能覆盖多步；本模块尚未导入录屏 manifest，不宣称 take 绑定已验证。`operation_key` 是同 job 的业务动作键，调用者必须保持稳定语义，不能靠改名规避 unknown；模块无法识别故意更换 job/key 后语义相同的动作。

根级 flock 序列化本执行器命令，并继承到 worker。控制进程崩溃时，worker 未退出仍持有 lease；不能凭 PID/TTL抢占。该 lease 不管 GUI，也不能阻止其他应用、剪映、录屏 Agent 或人类占用桌面。Skill 必须另向协调者取得桌面时段。

- 同 request_id、同参数返回原 receipt，不再执行；不同参数拒绝。receipt 是原请求快照，最新状态另查 status。
- 同 step 换新 request_id 也不重跑。新业务复测需旧操作终结后新 test_run；原片/证据不覆盖。
- 操作 intent 后 controller 中断，即使没有产物，status 也保守标 unknown。存在运行中的 inherited lease 时 status 返回 busy，绝不称成功。
- unknown 禁止自动重试；同 job/operation_key 的新 run 也 blocked。`result=null/succeeded=false/sql_candidate=null`。
- `reconcile --root … --run-id … --step-id … --request-id …` 仅接受同一 operation/script/input/worker 的完整本地 worker receipt，加真实产物哈希及冻结预期验证。不存在或不一致继续 unknown，不执行第二次。恢复时实际 exit 未观察到，`process_ok=null`；receipt 退出值另列。
- 外部 UI unknown 自动对账尚未实现，需后续可信适配器/独立人工查证；不可用文件缺失/PID不存在推断“未执行”。
- Popen 明确未创建成功才记 confirmed_process_not_started。只有固定本地 worker 超时，才能 kill 当前持有的 child object 并 wait，记本地失败/隔离；这不是对外部操作未知结果的处理方案。
- pending 请求崩溃后重放返回 interrupted_no_resubmit；确定性校验错误也消耗请求 ID，修正需新 ID，不能靠新 ID再次执行同 step。

## 结果与耗时

进程退出只决定 `process_ok`；产物符合独立冻结 expected 才 `result_verified=true`。exit 0+错误产物为 failed；GUI 进程退出不适用，为 null。已有产物校验没有语义质量或工具来源的独立证明，因此 handoff 永远 `ready_for_script_freeze=false`，保留独立语义 review。

全部步骤 succeeded 为 success；有成功同时存在失败/阻塞为 partial；无成功且有失败为 failure；只有阻塞为 blocked；pending/waiting 为 result null；任何 unknown 优先 needs_reconciliation。即使 optional 步骤失败也至少 partial，避免隐藏实际失败。

`executor_elapsed_ms` 为当前控制进程启动 worker 到 wait/log 收尾的单调时钟时长；其他终结调度分支也计时，但不是产品处理耗时。`worker_elapsed_ms` 为 receipt 的真实变换区间；失败/输入限制可能很短。启动、日志及验证开销不应包装成产品生成耗时。日志有 retained SHA/bytes 与 full-stream SHA/bytes，最多保存 16 KiB 首尾；truncated 标识明确，内存有界，不把完整流哈希冒充保存片段哈希。

## Supervised observation

以下桥接契约测试可以不碰桌面；正式 UI 操作必须遵循 Skill 和当前用户授权：

```json
{
  "schema_version": 1,
  "operation_id": "从run返回复制",
  "observation_token": "从run返回复制",
  "outcome": "succeeded",
  "note": "实际观察和已完成动作；不要填写推测",
  "evidence_files": [{"filename": "result.txt", "kind": "result_file"}],
  "product_limits": []
}
```

保存为某观察目录的 JSON，证据只能用同目录单文件名；`kind=result_file|screen|log`，最多 8 个、单个最多 4 MiB，不接受链接/路径越界。用 `observe --root … --run-id … --step-id … --request-id … --observation …`。先读取/验证完整输入，再复制到本运行内唯一私有目录。支持回报 succeeded/failed/blocked/unknown；操作员回报 succeeded 后，恰有一个匹配 expected 的 result_file 才 succeeded；单个真实文件明确不匹配为 failed/independent_result_mismatch；没有或多个结果文件、只有截图/说明则 unknown。GUI 源身份、操作过程、费用和产品语义不由这个文件哈希独立证明。

product_limits 每项 `{statement: 文本, evidence_index: 0}`，必须引用本次证据，写为 operator_attestation、independently_verified=false，不转成已证实产品事实。file_transform 限制来自实际 receipt，为 adapter_observed；scope/未知adapter 为 executor_scope。未知价格保持 null。

`observation_wait_ms` 从 operation intent 到回填，含人工/协调等待；当 wall/monotonic 差值不一致（超过 5 秒）设 null，不能保证跨重启计时。`active_ui_elapsed_ms=null`，不接受调用者伪造为已验证时长。Skill 可另提供有来源的操作时间记录供人工审核，但 MVP 不自动解释它。结果文件只证明字节满足预期；工具来源仍是操作员断言，不是第三方证明。

## 编剧接口与并行剪辑接口

`handoff --request-id …` 生成不可覆盖的 `handoff-<request>.json`，再次检查 frozen input/script 与所有证据字节、权限、归属 run 和哈希。篡改/缺失即拒绝；成功后重放返回原响应，想重新查证用新请求。Schema 是本模块新增 `screenwriter_test_handoff`，不是声称已有生产 SQL 表字段。

- `test_run`：固定身份、步骤执行状态、过程/结果分别验收、实际时间及证据路径/哈希。
- `facts`：仅验证过的本测试产物事实，有 evidence_refs 和 synthetic/provenance；不复制自由文案为已证实营销 claim。
- `gaps/limits`：失败、阻塞、unknown、未完成、限制值及来源；不能用“录屏好看”抹掉测试失败。
- `pricing/recording_link`：未观察/未导入保持未知；不为验收而公开私人结果。
- `do_not_say`：fixture不等于产品能力、scope阻塞不等于产品不支持、toast/exit不等于正确、unknown不能重提等。
- `ready_for_script_freeze=false`：等待独立来源/语义/隐私/总体成功判据审核。

给编剧的是本地工件，未自动发送。并行剪辑 Agent 可只读 test_run_id、evidence refs、timing scope、gaps，不能自动把 result evidence 当可发布镜头。音画/原片/镜头可用区间仍由独立录屏 manifest 验收，尚无自动 join。整个模块与剪辑目录、README、现有录屏状态存储完全隔离。

## 验证命令与未验范围

```sh
python3.12 -m unittest discover -s tests -p test_execution_agent.py -v
python3.12 -m execution_agent --help
```

已测试安全 fixture 真子进程、监督桥接契约和 TextEdit 真实 GUI 操作/结果回填。没有操作 Gamma、剪映或启动录屏。现有账号、新的软件能力、可分离的产品处理耗时、真实产品限制、录屏链接及实际业务产物语义均未验证。首轮摘要见 [validation](test-execution-agent-validation.md)，GUI 操作与补充测试见 [GUI validation](test-execution-agent-gui-validation.md)。
