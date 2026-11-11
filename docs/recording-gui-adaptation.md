# Mac GUI 适配与 Gamma 录制阻塞记录

日期：2026-11-03。代码由 Codex 在本 Mac 本地修订，尚未提交或推送。

后续显式单窗口、92项测试及真实Gamma媒体结果见[显式单窗实施与实测](recording-explicit-window.md)。本文保留系统picker阶段历史。

该阶段结果：无参数安全空闲入口、当前构建精确路径绑定、跨进程重用应用绑定、真实
录前取消及自动超时均已验证；85项测试和Swift编译通过。Gamma最终尝试仍未选中
来源或开录，没有MP4。后文先保留早期83项验证及超时历史，最新修复见文末。

## 实际问题与定位范围

先前两次第三方录制均未取得系统选择器的选择回调，没有开录或 MP4。
第一次等待超时退出2；第二次在无法操作选择器后主动取消，退出2。
两次均保留失败 take，确认桌面锁释放后才继续，没有覆盖或重试未知提交。

原失败 API 是 computer use 的 `cua.getApp("WindowRecorder")`，以及按已核实
可执行文件路径调用的 `getApp`；均返回 `Invalid app`，不是 ScreenCaptureKit
的权限拒绝。当时构建产物是没有 Info.plist、没有 bundle ID 的裸 Mach-O 文件。
`getApp("com.apple.controlcenter")` 则返回 `-10005: timeoutReached`。
这两类错误不能合并解释成“用户没给录屏权限”。

可用电脑操作接口为 `cua_repl`：原生 app 的 AX 状态、应用窗口截图、点击、键盘、
滚动，以及 Chrome 标签页控制。已用原生 Chrome 滚动实际切换现有 Gamma 演示页。
本轮没有使用 shell 键鼠注入、私有 AX 访问、TCC 修改或整屏截图替代选择器。
项目已有 `record-tool-demo` Skill；其早期 fixture 授权文字不是本次 Gamma
账号操作的授权来源，本次范围来自用户后续的明确指令。

Apple 的[官方 ScreenCaptureKit 说明](https://developer.apple.com/videos/play/wwdc2023/10136/)
支持从应用按钮呈现系统选择器，选择结果经 observer 返回 content filter。
应用按钮只是呈现入口，不应自行选源或把调用成功当作已显示、已授权、已开录。

## 本地有限修订

- `build` 输出 `AutoVideoRecorder.app/Contents/MacOS/WindowRecorder`，附标准 APPL
  Info.plist，bundle ID 为 `org.autovideo.WindowRecorder`。仍是同一 Swift 后端，
  Python 直接管理子进程和继承桌面锁，不改为 `open -a` 或另建后台服务。
- 构建缓存 hash 包含源码与 bundle 元数据。目录0700、Info.plist0600；已有不符
  元数据、越权文件权限或内部 symlink 直接拒绝，不覆盖或擅自 chmod。
- 编译使用新建私有临时目录，避免可预测的旧编译输出路径被当成覆盖目标。
  只有编译完成的可执行文件才通过 create-only 链接发布；失败不发布半成品。
- picker 模式增加正常、有限时长的 GUI 控制窗口，包含 `Choose target window`
  和 `Cancel recording`。前者只调用原系统单窗口 picker，且只允许呈现一次。
  单桌面锁、父进程/权限监视和原有超时仍运行。
- `picker_controls_ready` 是应用发出的控件就绪事件，
  `picker_presentation_requested` 只是 API 调用记录；两者都不证明实际 UI 可见。
- 选源回调仍要求 exactly one window、匹配期望 bundle/PID/可靠启动身份；
  窗口/权限/前台变化仍 fail closed。选择完成才收起控制窗口并激活目标应用。
  系统声音、麦克风、摄像头仍关闭，没有整屏或其他来源回退。

没有安装第三方软件、调用模型、发布或上传文件，没有更改系统安全设置或申请新权限。
构建物和运行状态仍在 Git 工作树外。

## 检查与实际结果

- `python3.12 -m unittest discover -s tests`：83项通过，4.052秒。
  保留原77项；新增6项覆盖 app 布局/私有权限与不启动进程、缓存复用、包装版本
  失效、异常元数据不覆盖、内部目录 symlink、公开权限拒绝及失败编译不发布。
- 实际 Swift 编译退出0；仅 build，没有触发录制。
- Foundation `Bundle(url:)` 实际识别正确 bundle ID 与可执行文件；随后只读查询
  LaunchServices 确认该 bundle 已被系统识别。这不等于 computer use 验收通过。
- 修订后只做一次 Gamma 尝试，使用先前生成的私有六页虚构中文演示，没有重新
  生成、购买、订阅或分享。目标 Chrome 窗口仍位于外接副屏，书签栏临时隐藏后恢复。
- 新后端权限预检 screen/AX 均 true；日志发出正确 bundle ID 的
  `picker_controls_ready`，`capture_started=false`。
- `cua.getApp(<实际 .app 路径>)` 不再报 `Invalid app`，但这次调用等待约208秒
  后返回 `Computer Use server error -10005: timeoutReached`。没有按下选择按钮，
  没有 `picker_presentation_requested`、源校验或 `recording_started` 事件。
- 后端按自身期限先退出2，终态 `quarantined/timeout_unknown`。
  只读检查确认没有活动录屏应用、桌面锁已释放，整个本次 Gamma 证据目录没有 MP4。
  stderr 只有 macOS 输入法初始化信息，没有捕获失败错误。

本次修订解决了裸 CLI 的包装缺项，但尚不能把它称为可控 GUI 或成功第三方录屏。
`getApp` 对正常 bundle 的窗口访问仍超时；现有证据不能确定工具内部超时原因，
也不能据此断言 macOS 系统选择器本身不支持自动化。当前没有已验证的受支持
computer use 点击路径，因此停止更多 picker/录制尝试，不换路径绕过访问限制。

视频时长、尺寸、帧、音轨和完整解码均未执行，因为没有视频。
Gamma 真实结果与录屏结果仍分开记录：六页生成和预览已有证据，PPTX 导出未完成，
Gamma 原片不存在，端到端 operator 未通过。

后续应先排查或修复受支持 computer use 的正常 app 窗口访问，再做不捕获屏幕的
GUI 控制验收。若需要更换进程/IPC 架构或新增系统权限，应先单独设计和 review，
不能删除当前父进程、继承锁、目标身份或隐私检查以求跑通。

本机证据保留原始失败 take、输入/拍摄计划和终态摘要；日志、账号文档标识、原片、
截图和本机路径不加入仓库。本文件是原创工程摘要，不是工具内部实现的诊断证明。

## 无参数入口修复及后续真实重测

已证实的启动缺陷是：旧版没有CLI配置参数就会在约16毫秒退出2，普通GUI冷启动
无法保持可读窗口。当前无参数入口进入独立 `IdleApplication`：显示明确的
`Idle — no recording session` 状态及Quit按钮，不查询屏幕权限，不创建录屏会话、
桌面租约或picker。未知参数、无效配置仍拒绝；有效录制路径继续经过原有配置、
继承锁、父进程、权限和来源身份检查。delegate生命周期显式保持到事件循环结束。

picker注册和激活推迟到 `Choose target window` 被点击时。在按钮之前只有正常
GUI控件，选择/取消窗口显示take ID；点击后再次核对期望进程的bundle、PID与启动
身份，再配置系统单窗口选择器。没有新的枚举、声音、摄像头、权限申请或全屏路径。
退出时仅在本进程实际激活过picker后才停用并移除observer。

最新检查与运行事实：

- 全套85项通过，8.417秒；新增两个测试使用真实、明确由测试创建的OS子进程，
  分别验证监督器超时后的TERM及拒绝TERM时的KILL宽限、wait回收和继承锁释放。
  它们不是ScreenCaptureKit媒体测试，不把模拟子进程当录屏证据。
- 当前Swift实际编译退出0，bundle版本为2；无效启动参数仍返回2。
- 真实无参数GUI出现，用户也确认看到了预期Idle窗口；本机只读AX读取约342毫秒。
  一次通过所属测试监督器终止并回收；后一次通过真实GUI Quit正常退出0。
- 直接使用bundle ID时，computer use明确报告两个历史构建目录共享同一ID，存在
  歧义。没有更改LaunchServices或删除旧证据；改用当前build返回的精确 `.app`
  路径，`getApp`约0.87秒成功。同一绑定读取重启后的真实会话约0.70秒成功，
  UI中的take ID与待操作会话匹配。不能将此结果推广为平台所有超时已修复。
- 真实 `fix-cancel-001` 在未选择来源时通过computer use点击Cancel，退出2，
  原因 `selection_cancelled`；终态stop查询成功，没有激活picker或生成媒体。
- 真实 `fix-timeout-001` 保持未选择状态约69秒后，经自身硬期限退出2，原因
  `timeout_unknown`；没有激活picker或生成媒体，桌面锁释放。
- 最终 `gamma-fixed-001` 只尝试一次25秒无声录制。控制窗口在约0.63秒读取成功，
  会话ID正确；点击Choose后记录 `picker_activated` 与
  `picker_presentation_requested`。系统ControlCenter完整路径的 `getApp` 返回
  `-10005: timeoutReached`，约5.56秒；Gamma窗口的AX/局部截图没有给出可操作的
  系统选窗确认控件。直接点击已观察到的Gamma窗口未取得选源回调；该点击与硬期限
  的先后没有独立计时，不能据此断言macOS不支持窗口点击选择。
- 本次正式尝试最终经硬期限退出2，`quarantined/timeout_unknown`。无
  `source_verified`、`start_intent`或`recording_started`，无MP4；窗口、进程、
  桌面锁均已清理，Gamma页面保留，书签栏恢复。没有重复生成或消耗Gamma点数。

视频时长、尺寸、帧数、音轨及完整解码仍未验收，因为没有视频文件。
App明确配置无声不等于已有零音轨视频。当前阻塞是系统选择器没有经可用computer
use取得可操作的选窗状态；不能把请求呈现、点击、应用窗口可读或安全测试通过当作
真实Gamma录屏成功。不继续重复picker，也不通过直接枚举/全屏录制绕过这一步。

本仓库没有实现对 `cua.getApp` 平台请求的真正取消；既往外层JS的10秒参数曾未能
及时结束调用。此次已验证的是项目自己的进程期限、取消及回收，并没有把
`Promise.race`或停止等待当成工具调用已取消。平台内部获取/激活/快照等待仍需
服务端分阶段日志，当前文档只给出实测边界。
