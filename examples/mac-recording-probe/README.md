# macOS 自建窗口录屏探针

来源：Codex 在 2026-11-02 本机验证过程中编写的临时探针；此目录保存当次使用的源码与复现说明。**不是 Claude 设计产物，也不是 auto-video 产品录屏后端或 desktop_gateway 的实现。**

只创建带测试文字和动画的 NSWindow，不操作其他软件。通过 `SCShareableContent.getCurrentProcessShareableContent` 只枚举本进程窗口，校验 window ID 与 owner PID，再用 `SCContentFilter(desktopIndependentWindow:)` 捕获该窗口。没有显示器或矩形录制回退。

`RecordingProbe.swift` 使用 macOS 15 的 `SCRecordingOutput`，明确关闭系统音频、麦克风；没有摄像头调用。请求约 7 秒录制，收到 finish 回调后退出；最终时长、字节和音轨以完成文件的 ffprobe/stat 为准。运行前、选源后检查前台，录制中每 0.1 秒检查前台应用和窗口可见性；检测到变化即停止并标记隔离。这是辅助保护，未证明对所有人工干扰均可检测。

## 运行边界

- 需要 macOS 15.0+、本机已有 Swift/Xcode SDK 与 ffmpeg/ffprobe；本目录不安装依赖。
- 仅在用户明确允许且执行进程已有屏幕录制授权的安全会话中手动复现。程序预检为 false 时退出，不主动请求授权，不打开系统设置，不变更 TCC。
- 辅助功能只做预检诊断，本探针不模拟键鼠，录制自己的窗口不依赖 AX 输入权限。
- 如果执行上下文或应用身份变化，应重新核实权限；当次 True 不能推广到其他进程。
- 运行时测试窗口会临时成为前台。不要在含私人信息的工作流中执行，不要在录制中切换窗口。中止、超时或失败的文件应保留并隔离，不算成功。
- 不要将原片、抽帧、日志或编译缓存放进仓库或发送给模型服务。每次使用新的临时目录，原片不覆盖。

## 人工复现示例

以下命令只作为后续人工复现说明；保存源码不表示这些命令又执行过一次。从本目录执行：

```bash
probe_dir="$(mktemp -d "${TMPDIR:-/tmp}/auto-video-probe.XXXXXX")"
swiftc -swift-version 5 -target arm64-apple-macos15.0 \
  -module-cache-path "$probe_dir/module-cache" \
  RecordingProbe.swift -o "$probe_dir/AutoVideoRecordingProbe"
"$probe_dir/AutoVideoRecordingProbe" "$probe_dir/take-01.mp4" \
  > "$probe_dir/events.jsonl" 2> "$probe_dir/stderr.log"
ffprobe -v error -show_streams -show_format -of json \
  "$probe_dir/take-01.mp4" > "$probe_dir/ffprobe.json"
ffmpeg -v error -i "$probe_dir/take-01.mp4" -f null - \
  2> "$probe_dir/decode.log"
```

必须检查探针退出状态、`recording_started`/`recording_finished`、完整解码退出状态，以及视频来源和无音轨；文件存在或 PID 消失不能单独判成功。此示例为 arm64 构建；其他架构需另行适配验收。程序超时退出表示结果未知，不能自动重录。

## 可选的本机离线抽帧文字检查

`ProbeFrameCheck.swift` 使用 Apple Vision，只处理传入的本地抽帧。它输出预期测试文字的匹配情况、动画计数及未预期文字数量；不输出未匹配文字内容，不包含网络上传代码。识别失败只返回错误域和代码，不能记为通过。

```bash
for second in 1 3 6; do
  ffmpeg -v error -ss "$second" -i "$probe_dir/take-01.mp4" \
    -frames:v 1 "$probe_dir/frame-$second.png"
done
swiftc -swift-version 5 -module-cache-path "$probe_dir/module-cache" \
  ProbeFrameCheck.swift -o "$probe_dir/ProbeFrameCheck"
"$probe_dir/ProbeFrameCheck" \
  "$probe_dir/frame-1.png" "$probe_dir/frame-3.png" "$probe_dir/frame-6.png"
```

抽帧检查不代替整片人工审看，也不证明真实产品输出正确、系统声音可录、30fps 恒定、多显示器隔离或无人值守能力。已完成的当次事实见 [本机验证记录](../../docs/mac-recording-validation.md)。
