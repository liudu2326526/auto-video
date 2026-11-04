# Mac 剪映剪辑 Agent MVP

这是受监督的本机 Agent：Python 标准库 helper + 项目 Skill + `cua_repl` 剪映操作。
不接入总控数据库，不是常驻服务，不注入或改写剪映私有草稿格式。
本专项扩展了历史方案仅计划 FFmpeg 的后端范围；历史录屏代码不修改。

2026-11-04 已在 Mac 剪映专业版11.7完成真实12秒测试：导入、时间轴拼接、中文字幕、配音、0.5秒叠化、原生MP4导出、退出保存并重开工程。随后复制工程为v2，为两句字幕添加白字黑描边并原生重新导出、重开验证；v1工程闭合快照和原成片哈希未变。媒体技术验收与全360帧静帧复核通过；用户观看这支v2测试视频，在被请示配音、字幕和转场效果后答复“可以的，符合预期”。已记录用户来源的两项人工验收收据，helper对本次attempt返回`verified_with_recorded_gui_review`。这仅是本支12秒测试视频的人工验收，不是所有项目或无人值守流程认证。[实际证据和边界](editing-agent-jianying-validation.md)。

方案优先实际调用了 Claude Code 2.1.310，但 188 秒后 API/DNS `ENOTFOUND` 失败，零 token、未生成方案。
此次接口与实现由 Codex 完成，摘要见[设计记录](editing-agent-design-record.md)，调用原始记录保存在本机本次任务工作区 `design/`。

## 输入与输出

协议为独立的 `jianying_edit_plan/v1`，不静默接受历史 FFmpeg `edit_plan v1`。
运行 `python3 examples/make_editing_fixture.py /path/to/new-fixture` 可得到完整、可重建的安全例子。

顶层字段：`schema / plan_id / script / output / assets / video / subtitles / narration / transitions`。
`script.lines` 是冻结字幕字典。素材键是逻辑 ID；每项含本地 `path / sha256 / rights / rights_basis`。
相对路径相对输入计划文件，拒绝 URL、非普通文件、素材哈希变化及未登记使用依据。
源视频限定 30fps CFR、2GiB 内、最多 18000 帧；输出最大 180 秒、偶数尺寸、30fps。
单视频轨等速连续，整数帧半开区间；每段的源帧数必须等于目标帧数，无缺口或重叠。
准备素材统一 contain 到完整画布，保持比例并居中补黑。MVP 不支持裁切、变速、叠加、BGM 或自定义混音。
未知字段直接拒绝，不能悄悄省略。

`narration.mode` 可为 `macos_say`（本地已安装 Tingting/Samantha、rate 100..250）或 `provided`（登记的音频素材，时长等于整条时间轴）。
本地 speech 按字幕区间逐句合成，语句超长时失败，不截断语音、不自动重写文案。
生成的系统语音仅作为测试声音；正式商业制作另核对选用声音的许可与审听效果。
`transitions` 只接受接缝 `dissolve`、1..30 帧、`timing=preserve_total`，实际在剪映应用。
如果目标剪映版本会缩短时间轴，应调整计划后新建版本，不能忽略总帧数变化。

```sh
python3 -m editing_agent validate /path/edit_plan.json
python3 -m editing_agent prepare /path/edit_plan.json --attempt /path/new-attempt
python3 -m editing_agent event /path/new-attempt project_created --evidence /path/receipt.json
python3 -m editing_agent verify /path/new-attempt --export /path/jianying-export.mp4 --project /path/native-draft
```

prepare 生成逐段静音 MP4、UTF-8 BOM SRT、48kHz stereo PCM `narration.wav`、源素材收据和 `import_package.json`。
FFmpeg 只用于合成测试源、选段/格式准备和媒体检查；helper 不生成最终成片。
程序准备选段与剪映原生裁切应分别报告。`final_video_rendered=false` 直到实际剪映导出；该标志不是业务成功证据。

attempt 不允许覆盖，准备失败保留 `failed_preparation` 状态与已有中间结果；修复输入后新建 attempt。
状态/事件用于恢复提示，不实现进程租约或 OS 权限隔离。共享桌面由统筹分配时段。

## GUI 证据与验收

每个 GUI checkpoint 使用独立 JSON 收据，包含 `event / app / project_name / observed / details`。
`observed` 写实际观察时间，`details` 写实际 AX 事实或截图说明，不写希望看到的结果。
导出 request/completed 额外含准确 `export_path`，project_reopened 含准确 `project_path`。
CLI 记录收据哈希。事件依次覆盖建工程、片段、字幕、配音、转场、导出请求/完成、退出重开、视觉与听审。
这些是操作者对 GUI 的事实记录，不能让 CLI 自动断言语义正确。

verify 要求准确原生工程目录中有 `draft_content.json` 或 `draft_info.json`，记录文件哈希；不解析或修改未知私有格式。
同时检查导出一个视频轨和一个音频轨、尺寸、30fps、准确帧数/时长、全程解码和非静音。
缺 GUI 检查时返回 `partial` 与退出码 2，完整媒体与操作审查记录齐全时返回 `verified_with_recorded_gui_review`。
素材包准备成功不等于剪映成功；无完整听审不填 `audio_review_passed`。

导出超时先核对剪映现场和目标文件，不直接重复导出。状态不确定记录 `export_unknown`，登录、权限、付费等阻塞记录 `needs_input`。
未知导出结果须用之后的真实 `export_completed` 对账；其他阻塞处理后用 `blocker_resolved` 事实收据解除。
最新导出请求没有后续完成，或仍有未解阻塞时，即使以前通过也只返回partial。
保留源文件、旧工程和旧导出，不上传、不发布、不购买。

与实测执行 Agent 的接口：实测提供本地录屏及不可变哈希、可用源帧区间、evidence ID、实际耗时和产品限制；编剧冻结逐句稿件，生产方据此建立本协议。
本模块不修改实测报告、不把剪短等待描述为真实生成耗时。
