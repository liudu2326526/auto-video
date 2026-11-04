# 单窗口录屏 operator MVP

2026-11-03 本地增量：后端为私有运行目录内的标准 GUI `.app`，无参数启动只显示安全Idle窗口；picker注册/呈现仅在点击选择按钮后发生。85项测试、实际编译、当前完整路径的computer use绑定、真实录前取消与自动超时通过；Gamma正式尝试仍未取得系统选窗回调，未开录、无MP4。以下原77项与fixture结论为首次验收历史，增量事实与限制见 [GUI适配记录](recording-gui-adaptation.md)。

状态：2026-11-02，本机原生实现，77项离线测试与编译通过，已完成一次自建安全窗口CLI录屏并通过父助手最终review。基线 main `289686fca6422c064f2bbfc0af1de93a5b7ad880`，独立分支 `work/recording-agent-mvp`；已合入 reviewed Mac 方案/探针，不合并录屏代码到 main。代码由 Codex 实施，未调用 Claude 编码。真实结果见 [MVP验证记录](recording-mvp-validation.md)。

## 实际入口与支持范围

- `python3.12 -m recording_agent build --root <运行目录>`：仅编译仓库内 Swift，不启动 GUI/录屏。现有 Swift/Xcode SDK、macOS 15.2 arm64、ffmpeg/ffprobe 必须已可用，不安装新软件。
- `record` 是同步、1–30 秒的录制命令，不提供假装异步启动的 `start`。默认 7 秒，录完会检查媒体；另一个本地 CLI 可在其运行期间 `status` 或 `stop`。
- `--mode fixture` 只创建生成的测试文字/动画窗口，并用 current-process-only API 选择该窗口。
- fixture 身份证明为执行中的当前PID、SCWindow owner PID、自建且保留的NSWindow ID和可见性严格一致，不依赖可能为空的launchDate，也不伪造日期。picker仍要求bundle/PID及选择前取得的可靠启动身份，缺失即拒绝。单个CGWindowID以CoreGraphics所需原始值CFArray传递，不用NSNumber对象代替；精确查询无关窗口不在范围内。
- `--mode picker --expected-bundle-id <已确认bundle> --expected-owner-pid <已确认PID>` 调系统单窗口选择器，只接受期望所属应用。用户选取的 filter 必须是一扇窗口，再核对 window/PID/bundle/进程启动身份。不会输出或枚举无关窗口列表，随后只查询该 window ID 的几何元数据，没有全屏或其他来源回退。此第三方路径仅已编译，尚未实跑，当前安全窗口授权不涵盖账号或第三方私人内容测试。
- 默认且仅支持无声：系统声音与麦克风显式 false，没有摄像头调用或音频开关。屏幕权限预检失败即退出，不弹窗申请。AX 只作诊断；没有键鼠或真实工具自动操作。

## 调用示例（review 后方可实际录制）

所有 runtime root 必须在 Git 工作树外，使用调用者批准的私人目录，不把媒体写进仓库；下面占位路径需替换，不能直接照抄执行。

```sh
python3.12 -m recording_agent build --root <private-runtime-root>
python3.12 -m recording_agent record --root <private-runtime-root> \
  --request-id req-001 --take-id take-001 --job-id job-001 --mode fixture --duration 7
python3.12 -m recording_agent status --root <private-runtime-root> --take-id take-001
python3.12 -m recording_agent stop --root <private-runtime-root> --take-id take-001 --request-id stop-001
python3.12 -m recording_agent recover --root <private-runtime-root> --take-id take-001
```

`record` 返回码：媒体与时长计划 validated 为 0，partial/unknown/quarantined 为 2，输入/路径等拒绝为 1；status/stop 命令成功读写返回 0，不代表录制或实测通过。没有全局新凭据、网络调用或模型调用。

## 持久状态和幂等

runtime root 内 `.recording-state/state.sqlite` 用 SQLite WAL/FULL 保存 request digest 和 take JSON，独立于总体方案的业务库。root/状态/take/build 目录必须为当前用户所有且 group/world 无权限，已有0755或0777目录直接拒绝，不擅自 chmod。新目录0700，DB/配置/控制/日志/manifest以0600独占创建，既有内部文件需通过权限、owner、regular/symlink检查；原片由077子进程umask创建并再次验权限。每 take 使用独占新目录 `take-<take_id>`，初始输出 `raw.partial.mp4`；禁止路径穿越、用户可控 symlink、已有 take 覆盖或把输出放进任何 Git 工作树。原子JSON通过fsync临时件后独占硬链接发布，不 replace 已存在目标。编译缓存也留在 runtime root。

状态：starting → recording → stopping → validated；显式早停的有效媒体为 partial，失败/异常为 quarantined 或 unknown。无显式早停时，时长默认必须在目标±0.75秒内（校准假设），即使收到 clean finish，7秒计划只得0.1秒也拒绝/隔离。显式早停须同时有持久化stop请求和后端 requested stop 意图；允许短有效媒体，但标 `partial/stopped_early`、`plan_complete=false`，不通过完整拍摄计划。validated 表示媒体、时长计划、原片源身份与无音轨检查通过，视觉隐私审看仍 pending，**绝不等同于真实工具 success**。`upstream_test_run_id` 只是未验证的关联标识；manifest 的 `tool_test_status` 始终 not_performed。

同 request_id/同操作参数重放返回原 take 状态，不再启动；同键异参拒绝，已占用 take 用新 key 也拒绝重启。stop 的同键重放读回状态，终态 stop 不再碰后端。record 未收到回包不能新 take；先查询，旧录制确认终结后才可业务重录。

幂等命名空间是固定的 runtime root；丢失回包时不可更换 root 绕过历史记录。锁仍持有时 status 返回最后观测状态及 last_observed_only，不把锁等同于当前录制或成功证明；锁已释放且无终态时转 unknown。

## 单桌面与 fail closed

固定的本机 UID 桌面 flock 不随 runtime root 或 TMPDIR 改变。Python 持有锁并把同一 open-description FD 传给 Swift；Python 失联时 Swift 仍持锁到退出，不能仅因监督进程消失让第二个录制进来。单独的继承锁进程测试覆盖这一点。文件锁是合作式互斥，不限制同 OS 用户手工操作或其他未接入工具，不能声称 OS 隔离、capability fencing 或完整 desktop_gateway。

后端每 100ms 检查目标前台、窗口存在/可见、几何、单显示器/缩放签名、进程启动身份、屏幕权限及父进程；任一不符停止并隔离，来源不明不继续。跨显示器窗口拒绝，跨屏/缩放挑战尚未验收。选择前的 stop、权限或父进程丢失也取消，不等待选择后再开录。磁盘余量/录制字节达到保护线时停止；时长、选择等待和终结均有界，无法正常结束保留 unknown。

stop 只写本 take 的严格控制文件，不依据状态中 PID 任意发信号。监督器超时只终止自己的 Popen 子进程。原片媒体、日志、输出路径、源 PID/bundle 等信息都是本机运行数据，不能入库或上传模型服务。

## 终结、恢复与媒体验证

成功要求实际后端 clean finish、匹配源身份、进程 exit 0，并用现有 ffprobe 确认一条视频流/零其他流、有效尺寸、有限时长、严格递增展示 PTS，再完整 FFmpeg 解码且零错误。文件和 PID 存在都不能单独证明成功。原片大小验收上限 256 MiB，尺寸上限 8192×8192 且总像素不超过 33,554,432；超界隔离。原片 VFR 保留真实 PTS，不承诺 30fps/CFR。

完整媒体通过后用独占硬链接发布 `raw.mp4`，不会覆盖另一文件；写 manifest、提交状态后才清理同 inode 的 partial 名称。模拟“已发布原片/manifest、未提交终态”崩溃可 recover，不覆盖原片和原 manifest。磁盘故障下仍以 unknown/quarantined 保守处理，不承诺完整断电耐久性。

recover 只在继承桌面锁已释放后读取有界日志并查验 clean finish/exit 与真实媒体；无完整证据保持 unknown。日志是当前用户可写的合作式状态，不提供恶意本地用户或篡改日志防护。不会根据 PID 不存在直接改成功，也不盲目重录。

## Manifest 和总体契约边界

`manifest.json` 是本 helper 的 `recording_agent_take` v1，不冒充已实现的 test_runs/capture_manifests SQL 适配器。包含 job/take、未验证 upstream_test_run_id、源身份/显示器/point-pixel 尺寸、真实媒体 hash/字节/时长/编码/PTS、无音轨、supervised=true、tool_test_status=not_performed、独立plan完成情况/早停/容差与待审看说明。

记录 UTC 与 native callback 的单调时间；ffprobe PTS 独立保存，callback 只作近似锚点，`pts_mapping_quality=callback_anchor_only_not_frame_accurate`，不能当作逐帧精确 host-clock 映射。跨重启恢复不伪造原监督器耗时。接入总体 SQL、逐帧准确时间关联和编辑区间需下一阶段独立验收。

## operator 流程和未实现项

可复用 Skill：`.agents/skills/record-tool-demo/SKILL.md`，岗位说明 `agents/record-tool-demo.md`。顺序为 researcher 交接 → 场景/固定输入/权利 → 真实探索 → 真实输出内容验证 → 拍摄计划 → 正式 take → 本机媒体/视觉检查 → producer 根据实际素材写最终分镜。CLI 没有执行或验证真实工具的能力，Skill 不能把记录文件冒充软件实测。

当前未实现：异步 start 服务、无人值守/分布式网关、OS 隔离/epoch fencing、自动软件操作/登录、可信人类批准服务、付费操作/对账、业务 SQL 入库、系统声音/麦克风/摄像头、固定30fps、完整视觉隐私识别、黑帧/冻结自动判定及平台发布。已新增 `--mode explicit` 与 `--validate-only`，接口见[显式单窗记录](recording-explicit-window.md)。停止时序修复后93项测试及Gamma复测通过，实际24.496667秒位于25±0.75秒门槛内；旧隔离片保持不变，完整人工视觉审看仍pending，详见[最新复测](recording-stop-timing.md)。第三方系统picker读取仍阻塞，不把新路径当作picker已修复。

## 本轮实际验证（review 前）

- `python3.12 -m unittest discover -s tests`：77项全部通过，其中保留 main 的35项发现测试，新增42项录屏/路径/状态/媒体测试；包含纯Swift身份策略实编译/运行的14个断言、0755/0777 root拒绝、私有状态文件、原子JSON不覆盖、异常过短完成隔离及显式早停partial。
- `python3.12 -m recording_agent build --root <仓库外运行目录>`：exit 0，实际编译当前 Swift 后端，没有启动它；`--help` 入口也已运行。
- 一项媒体集成测试用 lavfi 在临时目录生成 0.5 秒64×64无声色块，实际 ffprobe 与 FFmpeg 完整解码通过；没有任何屏幕、麦克风、摄像头或账号输入。
- 其余权限拒绝、错误选源、干扰和终结事件为显式 fake backend 的离线测试，不冒充 macOS 运行验收；覆盖 request 重放/冲突、禁止覆盖、root/symlink越界、继承互斥锁、停止重放、丢失执行变 unknown、异常/损坏媒体隔离及发布后状态提交前的恢复。
- 原有设计检查通过：23项总体契约fixture、16项发现文档检查；本地文档链接、个人路径、空白及 `git diff --check` 检查通过。
- 前两次fixture尝试分别因CLI启动身份兼容和CGWindowID数组表示错误在start前退出2，均无MP4；已确认退出后才用新request/take修复验证，未覆盖旧件。第三个take是唯一真正开录的一次：7.363333秒、1440×696、H.264、112帧、零音轨、全解码exit0，目标7秒±0.75秒内，plan complete。
- 终态status两次及同request的stop两次返回同一validated结果，原片与manifest的hash、mtime和字节均未变化，没有再次启动录屏。权限预检True且无新授权申请，root/take0700、运行文件0600，原片仍只保留本机仓库外。
- 父助手已完成实现/身份修订/实际媒体及幂等证据的最终review，发布范围仅工作分支；不再增加录制。详细原创摘要和哈希见验证记录，不上传原片、抽帧或日志。
