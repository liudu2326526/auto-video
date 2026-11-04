# 逐镜创意返修 Agent

目标是使每个镜头解释一个观众关心的问题，并在**实际导出后**记录相对于参考的差距。
`creative_agent` 是助手操作的校验和审查记录工具，不是自动审美模型，也不驱动剪映。

本轮方案来源：实际 Claude Code 安全 print 调用在 75 秒上限结束，本机退出 143，
stdout/stderr 都为 0 字节，没有可用方案、没有可确认的响应模型；供应商端取消和计费未知。
根据用户此前“如果不行的话就你来”的授权，由 Codex 设计、审核和实施。
没有把视频发送给该模型。

## 结构与操作

保留严格的 `jianying_edit_plan/v1`，不把未知的特效参数偷偷塞入旧协议。
另用 `creative_review_plan/v1` 绑定原 edit plan 的文件和 SHA-256。
输入还包括 references、shots、required_gates。参考视频只用于分析，不因下载而获得成片再用许可。
每个 reference 必须有 URL、真实观察摘要和本地证据文件及哈希。

每个实际 clip 都须有且仅有一个 shot：

- clip_id 对应冻结剪辑计划；时序和源帧沿用该计划，不由旁白时长反推。
- audience_question、one_focus、source_note、change、emotion。
- source_evidence 绑定原始证据文件哈希、显式 CFR30 的原始 in/out_frame 和观察，
  不只记录派生片段的 0 帧；范围须处于已 probe 的源媒体内。该源本身若已加速仍需如实说明。
- narration 和 caption 数组必须与冻结计划逐字一致。
- reference_ids、transition_purpose、missing_material_action。
- accents：effect/sticker/sound/graphic/transition 的唯一 ID、确切资源名称、作用、
  时间帧、许可依据和保护原始证据可见性的说明。时间不得跨出所属镜头。

```sh
python3 -m creative_agent validate var/creative/plan.json
python3 -m creative_agent audit var/creative/plan.json \
  --export var/editing/new-attempt/final.mp4 \
  --review var/creative/export-review.json \
  --output var/creative/new-audit.json
```

review 使用 `creative_export_review/v1`，同时绑定**当前计划及成片**哈希。
reviewer 只能表明 operator 或 user 的实际观察，不得冒充另一方。
每镜记录 reference_comparison、observed_gap、revision_result、verdict 和证据；
每个计划装饰资源必须有 applied/missing/uncleared 的实际落轨收据。
证据是归因于操作者的记录，哈希只能证明文件没有更换，不能证明其叙述为真。

## 不能相抵的门槛

六项必须完整出现，分别 passed/failed/pending：

1. same_content_comparison：同一任务输入和结果，不能拿另一个漂亮模板替代。
2. numbers_source_result：重要数字的原稿与页面同时可核对，不能只拍结果。
3. error_and_real_correction：问题有原始证据，修正动作可见；不知道原因就不归因给 AI。
4. phone_readability：按实际目标手机尺寸看关键文字、字幕、遮挡和安全区。
5. rights_privacy：原片、字体、贴纸、音效、外部情境素材逐项对应当前使用范围。
6. truthful_audio_picture：台词、字幕和实际操作对应；加速可见标记，耗时未知就不声称节省。

任何 failed 或 needs_revision 镜头会得到 needs_revision；pending、未听审、未连续观看、
缺资源都会得到 needs_review。只有全部完整的归因审查才得到 review_complete，
该状态也不代表专业水准、版权法律保证、平台表现或发布授权。
没有总分、自动完播率、指定循环轮数或“达到爆款”的标识。

## 实际返修循环

先用支持的电脑控制在剪映副屏操作，只处理本任务工程。每版另存工程和成片，
禁止私自写原生 draft 数据。查看可用资源及“商用”标签和使用说明；不购买、不接受新协议。
未核许可的资源留在候选，不交最终发布。装饰要帮助指向、对照、情绪或节拍，不能遮住证据。

完整看导出，再按镜头对照参考，记录具体时间和差距。技术解码、帧数、音量、波形相关、
联系表都不能代替连续观看和听审。不能实际听到音频时 audio_listened 必须为 false。
修改后产生新导出并重新绑定所有证据，旧拒收记录保持原样。
工具用 O_EXCL 写新报告，不覆盖已有报告或符号链接；它不是跨任务签名审批注册中心，
不能防同 UID 的恶意操作者另写一份虚假收据，也不能凭文件生成批准外部操作。

## 实现验证

单元测试使用明确标注的模拟媒体元数据，覆盖硬门槛失败优先、缺听审/观看、源文件/成片改变、
漏镜/漏资源、错字幕/旁白、跨镜参数、重复 ID、未知字段/总分字段拒绝、报告不覆盖。
这些测试不证明剪映中的实际效果。真实视频必须另做全解码、时间/帧、混音与逐镜验收。

本机的实际迭代、参考范围、原生资源使用和未完成项见
[Gamma 返修验证记录](creative-review-validation.md)。技术检查通过不改变其中的待验状态。
