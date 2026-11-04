# Agent 源码整合与旧录屏复用检查

2026-11-04，Codex 工程记录。本次按用户要求整合最新 Agent 源码到功能分支；媒体、工程、运行日志、凭据和数据库不提交。本记录不能当作新 Gamma 成片或无人值守流程验收。

## 源码整合

- Mac 起点 `4d9fb2defc71118c14e324d08238646bbdffa6a0`，分支 `work/recording-agent-mvp`。未切换/修改 main。
- 整合前将全部 64 个修改/未跟踪源码文件、原始 tracked patch 和逐文件摘要保存在本机仓库外私有快照；复制后重新比对原件。没有 stash、reset、清空工作区或丢弃他人改动。
- Mac 录屏更新、实测执行、剪映剪辑及各自 Skill/文档/测试先提交为 `d1698f3`。两份公开剪映记录中的本机绝对路径及 Library 私有资产ID已脱敏；具体交付位置、原始收据、历史媒体与哈希仍留在本机，没有移动它们。
- 从 origin 实际 fetch 到编剧提交 `9651e24b131b80b5b62d156d2ff91b9b77a4cc2b`，核验 FETCH_HEAD 相同，再 cherry-pick。唯一 README 冲突按内容合并，保留两侧入口和历史说明，得到 `4d4a9a4`；云端原提交仍在原功能分支，不改写历史。
- 编剧 review 发现 Python 布尔 true 会被 `==1` 当作 schema 1；已将 draft/test_report 两处限定整数，补两项拒绝测试。没有添加自动 final、执行批准或发布权限。
- 当前五个独立模块为 discovery_agent、scriptwriter_agent、recording_agent、execution_agent、editing_agent；CLI/Skill 各自负责明确契约，尚无自动串接/统一数据库/常驻服务。

## 代码检查

先前 Mac 回归选择 185 项，28.861 秒全部通过。编剧及版本类型修复整合后选择 **220 项，25.883 秒全部通过**：发现35、编剧35、实测执行52、剪映26、其余录屏/浏览器策略72。

全仓当前共 225 项，另有 **5 项生成帧 AVFoundation 测试本轮未重跑**：前一轮在 `MemoryCropWriter.startWriting()` 处返回 `AVErrorEncoderNotFound/-11834`，没有进入各自主要断言。它们是 authoritative_generated_frames、budget_rejection、external_non_atomic、geometry_change、wrong_dimensions 五个场景；不能把 220 项选择性回归称为全仓全绿，也不能用 FFmpeg 成功覆盖 AVFoundation 失败。

本机 SDK/Apple API 已确认该错误为编码器未找到；测试只构造生成像素，不调用屏幕捕获或 TCC。历史 host 编码成功记录支持执行上下文因素，但不是同一二进制的严格 A/B，外部资源竞争尚未排除。先前日志/进程查询被系统沙箱限制，没有关闭安全设置或换通道规避。根因仍需有界生成帧对照，而非重新录屏。

`recording_agent build` 正常退出0、stderr为空、`capture_started=false`；只构建，不启动 app/捕获。架构契约23项 fixture、发现设计16项文档检查及五模块 --help 均通过。这些文档 fixture 不等于生产网关已实现。

提交候选按明确文件清单检查：无媒体/二进制/数据库/日志、个人绝对路径、私有文档/Library引用或常见凭据格式；不会使用 git add 全目录夹带运行结果。README 记录实际模块入口及历史验证边界。

## 既有 Gamma 素材技术预检

用户指定复用之前的录屏，不重新生成 Gamma 内容或消耗点数。选用已去浏览器顶部的 `Gamma-operation-content.mp4`，原始未裁剪浏览器窗口文件不作为剪辑输入。原片/成片始终留本机。

本次重新计算摘要仍为：

`c2cd553713e4fccfcb81e237532f8cf0d231485485929d2561c3defd9f6566e1`

大小 5538129 字节，2304×1192，193 帧，24.486667 秒，H.264、零音轨、time_base=1/600。采用保留源时基的 FFmpeg 全解码，exit0、stderr0。普通 null 输出默认时基会把相邻 VFR 时间量化成重复 DTS；保留 demux 时基后无报错，不将这一封装诊断误称为媒体损坏。

源是 **VFR**，剪映 helper 当前只接收 **30fps CFR**；不能篡改元数据假装是30fps，也不能放宽验收。须在新私有 attempt 中做显式导入派生副本，保留原文件、原PTS/哈希与变换收据，说明静态页重采样/补帧和尾部舍入。派生片技术验收不证明原录屏本来是固定30fps。

## 后续旧素材验证范围

1. 源码发布并核验功能分支后，在新私有目录生成 CFR 导入副本；复核帧数、时间轴、完整解码、无音轨、比例/边界及原件哈希。
2. 基于实际已有画面形成受信创作稿，通过编剧 helper 输出待审 script/storyboard/manifest。不能用旧60秒虚构周报拍摄草案替换本片《星屿轻记》的真实画面；新旁白不得宣布未知的 Gamma 价格、速度或功能测试成功。
3. 编剧 package 与 `jianying_edit_plan/v1` 不是同一 schema；操作员显式核对逐句文案与帧区间映射，再 validate/prepare。现有发现候选数据不在本机 var/discovery/runs，真实候选到编剧这一段尚需迁移原候选；缺失时只可明确标为 integration fixture，不假称重新真实发现。
4. 协调共享 Mac 桌面时段，使用 edit-video Skill 与受支持 CUA 新建剪映工程，导入本次真实素材包、字幕和旁白，实际保存/原生导出/重开；不注入剪映私有草稿，不覆盖旧工程或成片。
5. 后验核对输出帧/时长/音轨/非静音/全解码、字幕画面与语义。新片需自己的连续视觉与听审，不能继承12秒测试片的用户验收；未覆盖项保持 partial。没有媒体上传/公开发布授权。

以上 GUI 成片步骤在本记录时尚未执行，桌面需由主任务安排。实测 helper 记录受控执行结果与失败；writer 输出始终待审且 execution/publication_authorized=false。源视频、支持性测试和真正产品业务结果分开记录，不把监督操作包装成无人值守自动化。
