# 真实实测与录屏 Agent 专项方案

文档状态：2026-11-02 review 修订稿；设计尚未实现或验收，不能作为产品已可用的依据。初稿由本机 Claude Code 编写，本次修订由 Codex 根据主审意见实施，详见 [专项 review](tool-testing-recording-review.md)。
仓库基线：`ff0c69beea2b6370d9bd3ad0ac310f60fd9b360f`。本仓库当前只有设计文档，没有产品运行代码；本文不声称任何链路端到端可用。
相关文档：[technical-design.md](technical-design.md)、[data-and-state-contracts.md](data-and-state-contracts.md)、[tool-discovery-agent.md](tool-discovery-agent.md)、[mac-recording-validation.md](mac-recording-validation.md)。
生产运行时仍是 Codex Agent Team（researcher/operator/producer/reviewer/publisher 五岗位，主聊天统筹）；本文不新造常驻 agent，operator 是现有岗位。Claude Code 只负责编写本方案。

## 0. 阅读约定

- 【事实】已在本次会话中实际观察到、有命令或产物支撑的结论，范围严格限定于所写内容。
- 【设计】本方案提出的做法，尚未实现或尚未验证。
- 【待验】明确未验证、需要在实现后逐项通过验收的事项。
- 任何结论若不在这三类之一，视为无效陈述。
- 【事实】本次实测的元数据来自 2026-11-02 UTC、macOS 15.2 arm64、Claude Code 2.1.310 的一次受限会话：no-tools print、安全模式、受限模式、空 MCP、关闭非必要流量，未关闭权限检查、未修改持久配置。视频原片、抽帧与日志只存在于本机仓库之外，未发送给任何模型服务，也未复制进仓库。

## 1. 输入与成功判据

### 1.1 场景与固定输入

【设计】每次实测是一个 `job` 下的一个 `attempt`，输入必须固定且可复现：

- `input_sha256`：指向 `assets.sha256` 的固定输入文件哈希（如待测样本、素材包、配置文件）。
- 权利与来源：researcher 在发现阶段交付 `source_url`、官网/发布页、许可证或使用条款摘要；AIHOT 之类只作线索来源，不能作为亲测证明。
- 场景脚本：一段人类可读的“待验证行为描述”，例如“导入样本 → 触发转换 → 产出文件可用系统默认程序打开且内容与预期一致”。
- 明确的“外部可查结果”：若工具输出是云端任务或账号态，需保存可在获授权账号内独立核验的最小结果 ID、查询方式与结果证据。可以是私人任务或文件，不要求公开状态页，不为验收而公开私人结果。标识与证据按访问范围保存、对外材料脱敏。

### 1.2 候选待验功能

【设计】researcher 交付的候选项至少包含：工具名、版本、官网、已核事实（有来源）、待实测功能列表、建议场景、通过门槛。operator 只对“待实测功能”负责，不复核营销文案。

### 1.3 账号、版本与环境

【设计】环境快照写入 `test_runs.environment`（JSON）：OS 版本与架构、工具版本、安装来源、账号类型（是否登录、是否付费档）、显示器与缩放、权限状态摘要。禁止写入凭据、Cookie、令牌、个人身份信息。

### 1.4 预算与付费提交边界

【设计】主聊天可编排或发起请求，不能代替人类批准。高影响动作须核对既有、由独立可信用户身份批准且仍覆盖当前目标、输入、参数与额度的授权；没有授权才走总体契约的可信批准通道，不把 Agent/主聊天文字或 `outcome_note` 当作授权，也不重复索要仍有效的批准。

每个有费操作按 [总体预算契约](data-and-state-contracts.md) 的 `operation_key` 创建操作级 reservation，准入后才发送；硬预算要求有可执行费用上界，未知费用保留敞口。提交前持久化意图、幂等键及 request digest，操作后保存结果 ID 并幂等结算。发送结果不确定进入外部操作 `UNKNOWN`，先查询/对账；未证实未发生时不重传、不释放预留。`outcome_note` 只作事实摘要，不能替代授权、预留或外部操作日志。

### 1.5 成功判据

【设计】`result=success` 必须同时满足：

1. 真实产出文件存在，且能被独立打开/解码，内容与预期一致；或外部结果可独立查询且状态正确。
2. 产出来自真实软件交互，不是人工伪造或替代文件。
3. 证据可追溯到同 `job`、未隔离的 `evidence` 行。
4. 记录实际耗时（含等待、安装、渲染时间），不只是“能跑通”。
5. 部分功能失败必须如实记 `partial` 并在 `outcome_note` 写清哪一项失败；被环境或权限卡住记 `blocked`；完全失败记 `failure`。
6. 【事实】`test_runs.result` 现有枚举为 `success/partial/failure/blocked`，`succeeded` 必须等于 `result` 为 `success`。【设计】不新增 `running`/`unknown` 到该枚举。

【设计】运行中的 attempt 不写入 `test_runs`：`test_runs` 行只在测试结束时落盘（`ended_at` 非空）。进行中状态由协调器侧的 attempt/lease 状态机持有，避免污染既有枚举与 `succeeded` 约束。

## 2. 阶段流程与交付

| 阶段 | 责任岗位 | 交付物 | 完成判据 |
|---|---|---|---|
| 准备 | operator | 环境快照、输入哈希、权限检查结果 | 输入可复现、环境字段齐全 |
| 真实探索 | operator | 探索记录（含失败与耗时） | 真实操作走通至少一次，或明确 blocked |
| 输出验证 | operator | 结果文件 + 打开/解码记录 | 文件可打开且内容正确，或外部结果可查 |
| 拍摄计划 | operator | 录制前“拍摄计划” | 起点可复现、动作序列确定、预期输出明确 |
| 正式 take | operator | 原片 + `capture_manifests` | 每个 take 独立目录，不覆盖 |
| 媒体与证据检查 | operator（自查）+ reviewer（复核） | `evidence` 行、解码结果、checks | 第 8 节验收项通过 |
| 交接 producer | operator → producer | 素材索引 + 实际时长 + 失败点 | producer 只用实际素材写分镜 |

【设计】拍摄计划与最终剪辑分镜是两种文档，不得混用：

- 拍摄计划由 operator 在录前写，内容是“我要做什么、从哪个可复现起点开始、预期看到什么”。
- 最终剪辑分镜由 producer 在录制之后依据实际素材写，内容包含 `segments`、解说定稿、真实时长。
- 【事实】录屏成功不等于工具成功：一段干净的录屏只证明“画面被录到了”，不证明被录的那个功能真的产出正确结果。功能结论必须由结果证据支撑。

## 3. Mac 后端比较

| 后端 | 来源隔离 | 音频 | 控制与状态查询 | 已验 | 待验 |
|---|---|---|---|---|---|
| ScreenCaptureKit + `SCRecordingOutput`（拟议适配器） | 探针固定以 `getCurrentProcessShareableContent` 只枚举自身窗口；生产路径须系统选择器或用户确认的目标 bundle/process/window，再以 `desktopIndependentWindow` 限定已核窗口，不能沿用“只录自身”来声称可录第三方 | 本次明确关闭系统声、麦克风；产品系统声另验 | 探针 start/stop/finish 回调完整；产品需实现可恢复状态查询 | 【事实】Codex 临时自建窗口探针 exit 0；800×388pt、Retina 2x、1600×776px；MP4 7.328333s、H.264、112 帧、1186759 字节、无音轨；平均约 15.23fps；全解码 exit 0、0 错误 | 【待验】第三方窗口选择/隔离、产品适配器、固定 30fps CFR、系统声、多屏与长录均未通过；不能将扩大枚举范围视为验收 |
| 系统 `screencapture`（人工监督） | 弱：屏幕矩形或窗口参数可能包含覆盖物；窗口级视频隔离能力未验 | 帮助中列出 `-v/-V/-g/-G`，语义与行为未实际验证 | 命令行式，缺少可查询的运行态；只能靠进程与产物间接判断 | 【事实】系统 `screencapture` 存在，帮助含上述参数；本次未用这些参数录制 | 【待验】窗口视频是否真正只含目标窗口、音频参数实际语义、退出与产物一致性 |
| FFmpeg / AVFoundation | 弱到中：面向设备/屏幕抓取，难以按进程隔离来源 | 取决于输入设备，未验 | 依赖进程与输出文件，无原生 take 状态机 | 【事实】ffmpeg 9.0.2、ffprobe 存在，`ffmpeg -devices` 列出 AVFoundation 输入 | 【待验】屏幕抓取的设备索引、缩放与 Retina 处理、音频设备选择、失败语义 |
| OBS | 未评估 | 未评估 | 未评估 | 【事实】常见应用目录未发现 OBS | 【待验】不能推断整机没有其他录屏软件；本方案不要求在未授权情况下安装任何工具 |

【设计】ScreenCaptureKit 独立窗口为优先候选。生产选源通过系统选择器，或在已授权范围内仅枚举目标应用所需的最小元数据、筛选后请用户确认目标 bundle/process/window 标识；记录选择范围，开始及恢复前复核所有者与窗口身份，防 PID/window ID 复用。不得保存无关窗口标题，不自动扩大目标或回退全屏。系统工具只有在同等来源限制已验证且授权覆盖时才可人工监督使用；做不到则 blocked，`supervised=true` 不是放宽隐私条件的许可。

## 4. 几何、坐标与窗口身份

【事实】探针窗口内容 800×388pt，Retina 2x，输出 1600×776px。

【设计】

- 尺寸一律双记：布局用 point，写入制品与 manifest 用 pixel，并显式记录 `backing_scale_factor`。禁止在文档里只写一个数而不标单位。
- 窗口身份至少记录经用户确认的 bundle、进程身份（含启动身份，不能只信 PID）、`window_id` 与显示器/缩放。任一项变化都暂停输入并停止或隔离录制，重新观察、确认来源后才可新 take；来源不确定时 fail closed。
- 坐标变换必须显式：NSScreen 全局坐标、CGDisplay 坐标、屏幕矩形裁剪之间要做换算；多显示器存在负原点时必须保留符号，不能以 `abs()` 或裁剪到 0 处理。
- 窗口源优于屏幕矩形：屏幕矩形抓取会把覆盖其上的窗口、通知、Dock 一并录进去。窗口源仍需验收，不能默认“选了窗口就干净”。
- 安全工作窗口：目标窗口需避开菜单栏、Dock、刘海区域与系统浮层，四周留出 padding；padding 数值为初始假设，需在真实产品窗口上校准。
- 录前重观察、恢复后重观察：开始录制前与任何恢复动作之后，都必须重新读取窗口列表、位置、缩放与显示器身份，并与拍摄计划比对；不一致则不开录。

【待验】多显示器负原点、缩放变更（拖到不同 DPI 屏幕）、窗口遮挡、锁屏/休眠恢复，均未验证。

## 5. 录制控制协议

【设计】控制请求关联 `session_id`、`request_id`、执行中的 `attempt_id`、`take_id` 及桌面 `lease_id/epoch`；这是拟议控制上下文，不是既有 SQL 外键层级。test attempt 产出一个结束的 `test_run`；capture attempt 引用同 job 的既有成功 test_run，可制作多个独立 take。复测创建新的 test_run，重录创建新的 take；二者不一一对应。

【设计】控制动作：

- `start` / `status` / `stop` 是显式动作，不做 toggle。幂等账本唯一键为 `request_id`，payload digest 包含动作、take、来源及参数：同 request_id + 同 take/参数返回已有状态或已有未知状态，不再调后端；同 key 不同 payload 拒绝 `CONFLICT`。
- 同 take 不同 request_id 的 start 也拒绝重复启动。stop 的相同请求重放返回已有停止/封存状态；已停止的 take 收到其他 stop 请求也读回终态，不再次操作后端。所有请求仍须通过当前能力/epoch 校验，幂等不使旧授权继续有效。
- 先持久化意图，再执行：写意图记录（session/take/参数/时间）→ 调后端 → 查询实际状态。意图与结果分开记录。
- 未知状态先查询：任何“超时、无回包、连接中断”都先进入 `unknown`（记在协调器的 take 状态机里，不写入 `test_runs.result`），调用 `status` 取真实状态，再决定重试或封存。
- 不以 PID 存在、文件存在、目录非空作为成功判据。成功判据是“后端报告已停止 + 产物可完整解码 + 区间元数据齐全”。
- 后端无可靠 `status` 时降级为人工监督：由人工确认开始与停止，双方在 `checks` 中签字式标注；不得伪称自动确认。
- 崩溃处理先撤销/排空在途动作，结合后端状态、进程身份及句柄确认旧录制已终结；PID 消失只是部分证据。无法确认时保持 unknown、阻断接管或业务重录并请求人工处理；未完成文件隔离为 `.partial`，不能算成功。
- 请求重放不是新 take。只有确认旧录制终结且拍摄起点重新验证后，业务重录才创建新 take/目录/时间戳，不覆盖原片或历史清单。超时本身不能触发新 take；有副作用的工具操作还须先完成外部对账。

【事实】探针的 `start/stop/finish` 回调完整、进程 exit 0，说明该后端具备可构建显式状态机的基础。【待验】产品适配器的异常路径（回调丢失、重复回调、停止超时）未验证。

## 6. 桌面租约、干扰与权限

【设计】桌面是单独租约资源，同一时刻只有一个持有者。未来 `desktop_gateway` 唯一持有输入、录屏与浏览器能力；所有入队与 dispatch 都校验 `epoch`。

撤销顺序固定，不可重排：

1. 禁止旧会话（拒绝新的入队与 dispatch）。
2. 落盘并递增 `epoch`。
3. 取消或排空在途请求。
4. 确认旧进程已终止。
5. 封存录制产物。
6. 资源 `free` 之后才授予新租约。

【设计】TTL 超时不等于死亡证据：租约过期只表示“可以开始撤销流程”，不表示旧进程已停。必须有独立的终止确认。

【设计】干扰与中断：

- 前台、焦点、窗口源/几何或权限变化时，默认立即暂停输入，并停止或隔离录制；记录最后可信时刻、检测时刻与受影响媒体区间，不边录边等待决定。重新观察并确认来源、授权和起点后，才按新 take 恢复；无法确定来源时 fail closed。
- 人工键鼠干扰不可完全避免；探针期间前台干扰监视器未报告干扰，但这只覆盖探针窗口场景。
- 权限被拒绝或撤销后立即停止，不绕过：不读 TCC 数据库、不改权限数据库、不改 SIP、不伪造授权。
- 权限按进程复检，且分项独立：屏幕录制、辅助功能（AX）、系统声音、麦克风、摄像头各自记录，不能用一个 `True` 代表全部。【事实】本次初始受限终端预检 screen/AX 为 False；后续获准执行上下文及录屏探针自身两项均 True；最终重连后复检仍 True。这不代表其他进程有权限。本次禁止麦克风与摄像头（探针 `captureMicrophone=false`、未打开摄像头）。
- 自己窗口录屏不依赖 AX 模拟输入：探针无需模拟键鼠事件，这一点降低了 AX 依赖，但不等于产品交互也不需要 AX。
- 【设计】macOS OS 级隔离边界：总体方案中的 Linux cgroup / X11 / Wayland 示意不能当作本 Mac 已存在或可直接运行的隔离方法。OS 隔离未证明前，只允许人工监督探针，不宣称无人值守，不宣称 fencing 通过。

## 7. 最小 test 报告与 capture manifest

### 7.1 最小 test 报告

【设计】字段对齐现有 `test_runs`：`test_run_id, job_id, tool_id, tool_version, environment(JSON), input_sha256, started_at, ended_at, result, outcome_note, succeeded`。

### 7.2 capture manifest JSON 示例（精简）

下列哈希为显式伪数据（空串的 SHA256），仅示范格式，未入库，不代表真实制品；假设示例 take 时长 9000ms。`t_in/t_out` 为原片内毫秒整数，左闭右开 `[t_in,t_out)`，满足 `0 <= t_in < t_out <= duration_ms`，不是最终剪辑时间轴。

```json
{
  "capture_id": "cap_0001",
  "job_id": "job_0001",
  "test_run_id": "tr_0001",
  "shots": [
    {"shot": 1, "asset_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
     "t_in": 1200, "t_out": 8600, "real_elapsed_ms": 9400}
  ],
  "gaps": [{"take_id": "take_01", "from_ms": 8600, "to_ms": 9000, "reason": "切换输出目录"}],
  "checks": {
    "manifest_schema": {
      "schema_version": 1,
      "units": {"t_in": "ms", "t_out": "ms", "real_elapsed_ms": "ms"}
    },
    "take_bindings": [{"shot": 1, "take_id": "take_01", "capture_attempt_id": "at_capture_01",
      "asset_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "duration_ms": 9000,
      "pts_mapping": {"media_timescale": 600, "first_pts": 0, "anchor_mono_ms": 0, "anchor_utc": "2026-11-02T00:00:00Z"}}],
    "supervised": true,
    "scale_factor": 2,
    "display_id": "0000",
    "focus_changed": false
  }
}
```

【设计】SQL 列保持不变（`capture_id,job_id,test_run_id,shots,gaps,checks`）；`checks.manifest_schema` 是拟议的版本/单位契约，`checks.take_bindings` 是拟议的 shot→take→capture attempt/原片/PTS 绑定。现有 SQL 没有 test_run 的 attempt_id 或 capture 的 take_id 列，以上关系须由协调器结合任务/attempt 记录校验，不假装已有外键；若独立列落库须另行迁移评审。

v1 校验器按示例列出的各层字段白名单和类型校验（尚未实现）：schema_version 必须为整数 1，单位必须显式匹配；缺版本的清单仅走显式 legacy 迁移（有单位/血缘依据、保留旧件）或拒绝，绝不默认 v1。未知字段、未知单位、重复 shot、无唯一 take 绑定、跨 job/跨 take、超出时长、与 gaps 重叠或 hash/attempt 不匹配均拒绝。一个 take 可有多个 shot，但其身份、时基与资产绑定必须一致，不能用一条跨 take 区间拼接。

### 7.3 SQL 映射与血缘

- `evidence` 现有字段：`evidence_id, test_run_id, asset_id, kind(screen/result_file/log/video_clip/network_trace), captured_at, t_start_ms, t_end_ms, description, quarantined`。视频区间的 `t_end_ms > t_start_ms`。
- 资产存 `store/objects/<前两位>/<完整sha256>`，SQLite 由协调器 `avcd` 唯一写入，Agent 不直接写业务表。提交走 `artifact.commit` / `stage.complete`。
- 原片不可变：attempt/take 分目录，不覆盖；`attempt` 或 `inputs_digest`/`revision` 变化遵循既有守卫。
- 血缘：`tool → test_run → evidence → asset 与区间 → claims/script_lines → segments`。
- 文案冻结条件：至少一次 `succeeded` 的 test、同 `job` 未隔离的证据支撑必要 claim、测试已结束。
- 【事实】完整媒体解码或成功弹窗都不能单独证明功能结论。

### 7.4 时钟

【设计】三个时基分开记，不混用：

- `started_at`/`ended_at`/`captured_at`：UTC。
- `real_elapsed_ms`：单调时钟（如 `mach_continuous_time`），含应用启动、写入等待等真实耗时。
- 媒体 PTS：按每个 take 的 timescale/first_pts 与 `pts_mapping` 锚点换算，不能用全局单一锚点混合不同 take；重启/休眠等时基变化需重建可验证映射，失去映射的区间不供剪辑。

【设计】可剪辑区间 `[t_in, t_out)` 应覆盖“真实输出出现且可读”的完整过程，前后留 padding（初始假设 300–500ms），padding 需按真实产品校准。区间不得跨 take，不得引用已 `gaps` 的部分。

## 8. 媒体验收

【设计】两条独立验收线，不能互相替代：

1. 结果证据线：真实结果文件能打开、内容正确、或外部结果可查；截图/解码成功不算。
2. 媒体证据线：
   - 完整解码：`ffmpeg` 全量解码 exit 0 且 0 错误。
   - 画面来源与覆盖：确认画面来自目标窗口，无未预期窗口、通知、光标遮挡。
   - 冻结/黑帧/掉帧：`blackdetect`、`freezedetect` 类检查，阈值均为初始假设，需校准。
   - 帧率与时间戳：同时记录平均帧率与名义帧率，不把平均帧率当作 CFR 通过依据。
   - 音轨验收与音画同步验收分开：无声任务允许“无音轨”通过，但不能据此宣称系统声音通过。
   - 【事实】本次探针完整解码 exit 0、0 错误；1/3/6 秒抽帧在 Apple Vision 离线检查匹配预期文字，未预期文字数 0，动画计数 36/66/111 变化；无音频流；平均帧率 15.23fps。
   - 【待验】没有完整逐帧人工审看，没有刻意遮挡挑战，没有系统声音与音画同步实测。

【设计】以下是可执行的默认假设，实施前用合规测试素材校准并在任务冻结参数；不是本次探针已执行的质量测试：

| 检查 | 默认假设与判定 |
|---|---|
| 隐私/来源/权限 | 任一帧出现未授权来源、错误窗口或私人内容，或来源/授权无法确认：硬 fail，立即停止/隔离，不因可剪掉而通过 |
| 文件与覆盖 | 文件不存在、hash 不符、完整解码非 0 或任一解码错误、必需操作/真实输出缺失：fail；实际输出内容错误也 fail |
| 黑帧 | `blackdetect` 初始 pixel threshold=0.10、picture ratio=0.98；未在拍摄计划说明的连续黑段 >=250ms 进人工复核，遮蔽必需动作/结果则 fail，解释未明不通过 |
| 冻结/间隙 | 预先标注动态区间，`freezedetect` noise=-50dB、duration=1s；冻结 >=1s 或相邻展示 PTS 间隙 >250ms 进复核，关键动作丢失或动态间隙 >1s 则 fail；计划内静态停留单独标注 |
| 原片 VFR | 保留原始 PTS，校验展示顺序不倒退、时基可转换（处理 B 帧重排）；平均帧率不是 CFR 证据，无关键内容缺失才可使用 |
| 最终 30fps/CFR | 另验最终成片：帧间展示时间为 1/30s，容差假设 <=1ms，帧数与时长误差 <=1 帧，并检查关键动作/音画未因重采样失真；本次未执行 |
| 无声任务 | 音轨数必须为 0；意外录入音频按隐私/范围错误硬 fail，本次无声探针符合音轨数要求 |
| 系统音频任务 | 必须有可解码且非空的有效音轨；获授权已知有声校准段至少 200ms 的 RMS >-60dBFS，该段连续静音 >=1s 则 fail；仅有音轨头不通过 |
| 音画同步 | 系统声任务用已知同步标记单独校准，偏差默认绝对值 <=100ms，否则 fail；本次无声探针未验 |

验收结论 `pass/pass_with_caveat/fail` 仅为拟议 `checks` 内媒体结论，不改变既有 SQL 状态枚举。pass 要求全部必需结果/媒体/授权条件通过；pass_with_caveat 仅能注明不影响必需成功判据的可选限制，不能豁免安全、错误来源、损坏、真实输出错误或必需音轨/同步失败；不确定的必需项保持待查，不能先通过。

## 9. 指令模板、故障矩阵与实施步骤

### 9.1 operator 指令模板（仅设计，不安装配置）

> 你是本次 job 的 operator。输入：`job_id`、`input_sha256`、候选工具与版本、待实测功能清单、通过门槛。
> 步骤：① 记录环境快照与权限分项状态；② 真实探索，成功一次后记录耗时；③ 验证输出文件可打开且内容正确，或外部结果可查；④ 写“拍摄计划”（起点、动作、预期输出）；⑤ 从可复现起点录制 take，每 take 独立目录；⑥ 自查完整解码、来源、冻结/黑帧、音轨；⑦ 用 `artifact.commit` 提交并填 `capture_manifests`；⑧ 如实交接 producer。
> 约束：不覆盖原片、不伪造结果；失败/partial/blocked 如实记录；高影响动作核对可信用户批准和操作级 reservation；UNKNOWN 先对账；来源/前台/权限变化先暂停输入并停止或隔离，重新观察前不恢复；权限被拒即停止，不绕过。

### 9.2 故障矩阵

| 故障 | 检出方式 | 处置 |
|---|---|---|
| 权限被撤销 | 按进程分项复检为 False | 立即停止，记 `blocked`，不绕过、不弹窗猜测 |
| start/stop 回复丢失 | 超时无回包 | 进入 unknown，查后端实际状态，再决定重试或封存 |
| 录制进程崩溃 | 进程消失、文件句柄失效 | 确认已停，封存 `.partial`，新 take |
| 用户切前台或来源不明 | 前台/窗口源监视 | 立即暂停输入并停止或隔离，记录受影响区间，重观察前不恢复 |
| Retina/缩放变动 | 窗口与显示器重观察不一致 | 不开录或重录，重写坐标参数 |
| 黑帧或无声 | 第 8 节冻结参数检查 | 必需内容/必需声音缺失 fail；无声任务音轨 0 正常，安全错误不可 caveat 豁免 |
| 输出文件错误 | 打开/解码失败、内容不符 | 记 `failure` 或 `partial`，不剪辑该段 |
| 旧 epoch 请求到达 | dispatch 校验失败 | 拒绝，不执行 |
| 磁盘满 | 写入失败、空间检查 | 停止录制，保护既有原片，记 `blocked` |
| 证据跨 job | 血缘校验 | 拒绝引用，claim 不得冻结 |

### 9.3 最小实施步骤

1. 保留探针“固定只录自身”的限制；未来在 `desktop_gateway` 实现单独的第三方目标路径，经系统选择器或用户确认标识后最小枚举/筛选并复核，不回退全屏；该产品路径仍待验。
2. 实现 take 状态机（start/status/stop + unknown 分支）与意图持久化，先不接剪辑。
3. 打通 `artifact.commit` 与 `capture_manifests` 写入，跑通一条无音频的最短链路。
4. 接入媒体检查（解码、黑帧、冻结、帧率、音轨存在性），阈值全部标注待校准。
5. 用真实产品窗口做一次多显示器与 Retina 变更挑战。
6. 补齐权限分项复检与拒绝即停路径。
7. 在 OS 隔离未证明前，只跑人工监督探针，明确不宣称无人值守。
8. 通过上述验收后，才允许 reviewer 复核并交由 producer 写最终剪辑分镜。
