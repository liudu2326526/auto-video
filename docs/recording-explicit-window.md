# 显式单窗口与无捕获验证

2026-11-03，本 Mac 本地实现与工程记录。方案初稿由现有 Claude Code 生成，
经 Codex review 后由 Codex 实施。未提交、未推送；不包含本机原片、运行日志或账号标识。

## 已实现的接口

`record --validate-only` 完成目标身份、窗口几何、显示器与前台检查后写
`selection.json`，终态为 `source_validated`。这是选源验证，**不是录制成功**。
Swift 分支在构造任何 SCStream / SCRecordingOutput 之前退出；不会生成媒体。
复用既有请求/take幂等与合作式桌面锁，状态、stop、recover 可以读取这一终态。

`--mode explicit` 使用一次公开 SCShareableContent 查询，取得精确 SCWindow 后构造
`SCContentFilter(desktopIndependentWindow:)`，无需系统 picker 的 UI 自动化。
默认关闭系统音频、麦克风、摄像头及子窗口，不存在整屏或其他应用来源回退。

先有可信用户对具体目标与元数据范围的批准，才可执行。这个 CLI/Skill 自身不授予
账号访问、付款、发布或扩展采集范围的权限。

## 元数据与身份边界

- API 的一次 on-screen 查询可能向本进程返回其他应用元数据，不能称为“只枚举目标PID”。
- 代码先按用户指定的 owner PID 过滤，再读取候选的 bundle、几何、层级和窗口ID；
  不读取窗口标题，不记录无关窗口行。超过2048窗口拒绝，不做二次宽范围查询。
- validate-only 要求显式 bundle/PID、期望显示器ID及 x/y/width/height。
  仅接受一个可见、普通层级、几何匹配的候选；缺失或歧义拒绝。
- 正式录制还要求先前选源回执中的 CGWindowID 和进程启动 epoch；
  Chrome 的内部 window ID 不能用作 CGWindowID。
- 查询前、回调后、accept与开录前重新核对进程身份；按精确CGWindowID读取描述。
  几何/显示器/缩放/权限/前台应用/父进程变化仍停止或隔离。
- PID、epoch、窗口ID和桌面锁是同UID合作式防错模型，不声称抵抗恶意同UID进程。

## 使用顺序

下列均为命令模板，尖括号由本次实际获准目标替换；运行目录必须私有且位于Git工作树外。

```sh
python3.12 -m recording_agent record --root <private-runtime-root> \
  --request-id <validation-request> --take-id <validation-take> --job-id <job> \
  --mode explicit --validate-only --duration 1 \
  --expected-bundle-id <bundle-id> --expected-owner-pid <pid> \
  --expected-display-id <display-id> --expected-bounds <x> <y> <width> <height>
```

核对 `selection.json` 的 `source.window_id`、`source.process_launch_epoch`、
`source.bounds`、`source.display_id` 与实际目标；验证不会锁定未来窗口或保证之后仍存在。
正式录制使用新的request/take，但必须确认之前的操作已终结：

```sh
python3.12 -m recording_agent record --root <private-runtime-root> \
  --request-id <record-request> --take-id <record-take> --job-id <job> \
  --mode explicit --duration 25 \
  --expected-bundle-id <bundle-id> --expected-owner-pid <pid> \
  --expected-owner-launch-epoch <epoch-from-validation> \
  --expected-window-id <cgwindowid-from-validation> \
  --expected-display-id <display-id> --expected-bounds <x> <y> <width> <height>
```

这仍是同步record，不是异步start服务。validation-only与record的参数不同，不能重用
同一request。旧take不覆盖，UNKNOWN先对账；无完整证据不自动重录。

## VFR验收修复

旧的完整解码命令把解码帧送至null输出时，默认编码时基可将相近VFR帧量化成相同DTS，
即使输入PTS严格递增且解码退出0，也会产生null muxer报错。
现在使用 `-fps_mode passthrough -enc_time_base:v demux` 保留时基进行空输出检查；
仍保留 `-xerror`、非零退出或任何error stderr拒绝、独立ffprobe PTS及计划时长检查。
这不是过滤错误信息或将原片转换成CFR；原片不修改，不承诺30fps。
依据：[FFmpeg官方选项](https://ffmpeg.org/ffmpeg.html)。

回归测试用本地lavfi生成相邻帧间隔约1/600秒、其后较大间隙的无声VFR文件，
实测保留20帧及严格递增PTS，并完成无错误解码；不使用真实录屏作测试fixture。

## 本机实际结果与失败记录

以下保留92项测试阶段的历史。停止定时器后续已修复，93项测试与Gamma新take通过；
最新结果及隐私审核范围见[停止时序修复与复测](recording-stop-timing.md)。

- 92项测试通过（9.830秒），Swift实际编译退出0，diff检查通过。
  新增范围包括源验证的独立终态、重放冲突、无媒体保证、错误目标/几何/显示器/
  启动身份拒绝，以及实际Swift唯一候选策略与VFR媒体回归。
- 自建窗口的真实validate-only退出0，source_validated，未建流/未录制/无媒体。
- Gamma副屏的真实validate-only退出0，精确单窗、1倍像素缩放、1400×1000；
  无picker，source_validated，无媒体。查询及身份校验都通过。
- 三个早期短take未通过，保持隔离：第一个4秒静态计划只得1.313333秒、2帧；
  第二个UI操作晚于录制结束；第三个滚动没有实际切换内容。
  不放宽时长门槛，不用它们证明操作成功。静态来源的媒体时长仍是已知限制。
- 第四个短take通过正常Present入口和实际键盘切页录到5.271667秒、1400×1000、
  H264、72帧、零音轨。原状态由旧VFR验收命令隔离，保留这一历史状态；
  修正后独立完整解码退出0、无错误、PTS严格递增。本机9个抽样帧均不同，
  OCR匹配首页及第二页标题与正文，确认真实切页。
- 随后仅进行一次正式25秒计划：记录到26.251667秒、1400×1000、H264、195帧、
  零音轨。开始至stop_intent实际26.250282秒，后端正常退出0，无干扰/权限失败事件。
  由于超过25±0.75秒门槛，**仍是quarantined，没有通过完整拍摄计划**，无成功manifest。
- 正式原片独立完整解码退出0且error stderr为空，PTS严格递增（首0、末25.5秒）。
  本机8个有效抽样帧OCR依次匹配首页、三大困扰、核心功能、四步工作法、虚构学生场景、
  三步工作法等六页内容。未重新生成Gamma内容、未消耗点数、未发布或导出PPTX。
- 正式原片SHA256：`f9b7c57288a9a7d22bb310176bc56f16d93a1a57aa2816ba880ddd05a1d81692`。
- 所属helper进程已退出，合作式桌面锁确认释放；Gamma恢复首页，书签栏恢复。
  原片/抽帧/日志/运行配置均仅本机保存，不上传仓库或模型服务。

OCR和抽帧只是内容核对启发式，不能证明所有帧无隐私，也不能代替人工完整视觉审看。
未标记 `tool_test_status=success`；此helper仍只负责录制，Gamma生成结果是另一项实测事实。

## 仍需处理的限制

当时定时停止使用主队列asyncAfter；该次实际stop_intent比25秒计划晚约1.25秒。
日志证明停止时序超出校准容差，尚不足以确定具体阻塞调用。应在下一次设计/review中
评估独立单调期限控制、线程安全的停止状态与真实取消/封存，然后再验证；不能单纯放宽
容差或把系统API请求当成精确计时保证。本轮没有为取得成功而追加正式重录。

系统picker的CUA读取超时尚未证明已修复；本次成功到达真实开录使用的是独立公开API路径。
静态画面恒定时长、固定CFR、系统音频、同应用内其他前台窗口检测、无人值守和恶意本地
进程隔离仍未验收。录屏仅单窗；副屏位置与源ID不能替代权限和来源确认。
