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

## 原创情境动态的准备

`motion` 是独立、显式的准备命令，不是审查通过或剪映落轨。
本次扩展的 Claude Code 调用另有 45 秒上限，实际超时、本机退出 143、无正文；
依用户允许的回退，由 Codex 设计、审核和实施。没有向供应商发送媒体。

```sh
python3 -m creative_agent motion var/creative/original-motion.json \
  --output var/creative/new-motion-attempt
```

此命令额外依赖本机已有的 Pillow、FFmpeg 和 macOS STHeiti 字体；不自动安装。
审查命令不会导入 Pillow。输入严格使用 `original_editorial_motion/v1`：
`schema / id / source{path,sha256} / annotation_area / evidence_areas / kind / items`。
矩形均为源视频像素，字段为整数 `x/y/width/height`。示意输入：

```json
{
  "schema": "original_editorial_motion/v1",
  "id": "review-advice",
  "source": {"path": "silent-clip.mp4", "sha256": "<实际文件的64位SHA-256>"},
  "annotation_area": {"x": 10, "y": 290, "width": 620, "height": 180},
  "evidence_areas": [{"x": 0, "y": 0, "width": 640, "height": 260}],
  "kind": "review_advice",
  "items": ["数字", "措辞", "完成程度"]
}
```

源片只接受单视频轨、无音频、240..3840 偶数尺寸、最多 300 帧，显式检查每帧 PTS
为从零开始连续 CFR30，不能仅凭平均帧率。注释区宽 300..1024、高 160..512，
须在画布内且与 1..12 个证据矩形严格不相交；未知字段、越界、布尔坐标、源哈希变化拒绝。
证据矩形由操作者声明，这个检查不能理解证据内容或批准构图。

支持 `paper_stack`（文件卡片轻微移入）、`review_advice`（三项建议顺序高亮）和
`office_pause`（原创几何办公人物将纸张伸出、停下、收回）。前两类三项文字均为不超过
10 字的无控制字符短文本，固定显示“情境示意 · 编辑提示”。`office_pause` 的注释区至少
600×300，第一项纸张标签最多 4 字，后两项动作提示各最多 8 字，固定显示
“情境动画 · 非产品界面”；前 35% 伸出、中间 30% 停顿、最后 35% 收回。
该人物和空白显示器是原创抽象示意，不是真人、产品窗口、真实文件或新实测证据。
不生成仿冒产品控件、完成勾选或实际用时，不下载图片/表情/配乐。
`office_reaction` 沿用 office_pause 的空间、标签与纸张时序约束，额外用微笑递交→抬手惊停→
收回并指向核对的三个原创表情/身体姿态表达决定。原 office_pause 输出保持不变。
该类型依然是明确标注的虚构情境，不是顾客反应、真人表演或新的软件操作；姿态差异可检测，
幽默与节拍是否成立仍需要实际连续观看。
实际副屏替换、姿态对照及逐项使用依据见 [v17 记录](gamma-rights-and-v17-review.md)；
该片仍缺连续视听、实机与完整发布范围验收，不能用测试通过替代。
逐帧生成注释区 RGBA，最大单帧输入约 2 MiB，不缓存整段原始帧；这不是操作系统内存配额。
编码子进程有 90 秒 watchdog，库调用可指定 1..120 秒；超时杀掉本次编码器，
取消时回收本次进程、记录失败并保留 partial，不把文件存在当成成功。
它不提供对任意 Python 帧生成器的硬实时中断，预检和解码另有各自的有界超时。

新输出目录 0700、拒绝已存在目标、用户符号链接祖先和 `..`；完整解码、帧数、尺寸、
静音及源哈希复查后才独占封存 `original-motion.mp4`。此处只在指定区域合成像素，
有损重编码仍可能改变区域外的像素值，不能声称原证据逐像素无损。
`result.json` 明确 `source_rights_unchanged=true` 和
`automatic_semantic_or_rights_approval=false`。原创叠加不授予底片、字体、配音的权利，
外部素材须按[素材协议](material-agent-mvp.md)保存真实用途复核或已委托的受限助手选择，不能冒充 human。另记 accent、实际导入剪映、原生导出及逐镜复核收据。

声音可在本机生成不表示可公开使用。当前 macOS Sequoia 系统声音的
[Apple 许可 §2F](https://www.apple.com/legal/sla/docs/macOSSequoia.pdf)
限制公开分享及商业等使用，不能把系统 TTS 标为自行拥有的商业配音。
已有试片保留历史原样并另记发现；公开交付前须更换有适用许可的配音、重新导出和实际听审。

现可用独立 [本地中文预设配音准备](local-preset-voice.md)替换受限系统声音。
`voice` 使用显式核查的固定模型/音色和冻结时段，不自动下载、克隆声音或通过听审；
准备成功后仍需剪映实际替换、新导出及完整视听复核。

## 明确跳剪与停帧

当真实操作等待太长时，`retime` 可准备有明确源帧映射的静音选段，不能生成新产品动作或用时证明。
它由 Codex 在既有回退授权下设计、审核和实施；本轮没有重试此前被拒的 Claude 外传调用。

```sh
python3 -m creative_agent retime var/creative/retime-plan.json \
  --output var/creative/new-retime-attempt
```

输入 `editorial_retime/v1`，必填 `schema/id/source{path,sha256}/segments/notice_area/evidence_areas`。
每个 segment 只含 `source_in_frame/source_out_frame/output_frames`，源区间左闭右开：
正常播放须 output_frames 等于区间长度；延长只允许单个真实源帧的 hold。
最多 12 段，只能按源顺序向前，禁止倒放、重叠回跳、隐含变速、布尔帧号、越界或超过 300 输出帧。
不标注“原速”或实际产品耗时，因为源片本身可能已经被剪辑/加速。
源须为单轨静音视频、240..3840 偶数尺寸、最多 300 帧，并逐帧验证零起点连续 CFR30。

必须保留可见的固定“已跳剪”“含停帧”说明，内容由实际映射类型决定，不能自定义隐藏。
notice_area 为源像素 `x/y/width/height`，宽 300..1024、高 40..100，须与明确声明的
1..12 个证据矩形不相交。文字缩到区域能容纳的大小，否则拒绝；这不自动保证手机可读。
调用者应在手机尺寸实际看过说明，必要时增大区域并用新 attempt 准备。
矩形和原始画面的意义仍是操作员声明，代码不能理解某段是否属于等待或画面是否支持台词。

新目录 0700，拒覆盖、用户符号链接祖先和 `..`。编码命令有 90 秒超时，Python subprocess
超时会杀掉并回收本次子进程；probe/解码分别有界，不承诺整个函数的硬实时上限。
输出静音、尺寸、精确帧数、连续 PTS、完整解码和源哈希复查后才独占封存 `retimed.mp4`。
result.json 保留每段输入及输出区间、停帧数量和说明；失败保留 partial，不写最终批准。
准备成功不是剪映落轨或审查通过。新计划必须说明跳剪/停帧，不改变旁白就不能据此自动缩短字幕，
仍需新原生工程、导出和实际逐镜/听审。

新增 6 项测试含模拟边界/错误映射，以及真实双色静音片的选段、停帧、完整解码、
源证据颜色保留、拒覆盖/符号链接和真实编码超时。它们不是产品操作或审美验收。

## 显式平台遮挡模拟

`python3 -m creative_agent simulate var/creative/masks.json --output var/creative/new-mask-attempt`
准备本地静帧和区域相交结果，不操纵手机、浏览器或剪映。结果不批准实际可读性、隐私或发布。
输入 `platform_occlusion_simulation/v1`，必填：

```json
{
  "schema": "platform_occlusion_simulation/v1",
  "id": "declared-example",
  "source": {"path": "export.mp4", "sha256": "<实际文件的64位SHA-256>"},
  "profiles": [{
    "id": "assumption-360",
    "viewport": {"width": 360, "height": 640},
    "basis": "操作者声明的示例遮挡，非平台官方规格或实机结论",
    "blocked_areas": [{"x": 0, "y": 500, "width": 360, "height": 140}]
  }],
  "regions": [{
    "id": "caption",
    "in_frame": 0,
    "out_frame": 30,
    "rectangle": {"x": 100, "y": 1370, "width": 880, "height": 110},
    "observation": "操作者声明的字幕区域；示例不等于自动识字"
  }],
  "anchor_frames": [0, 15, 29]
}
```

严格字段、唯一标识、源哈希、单视频轨、尺寸 240..3840、r/avg 两个帧率字段均为 30/1、
帧数 1..18000。此命令不逐帧校验 PTS；最终剪辑验收另查时间戳和完整解码。
1..3 个视口宽 240..1440、高 240..2560，各 1..8 个遮挡矩形；
1..100 个重要区域和各自有效帧区间，1..40 个排序去重整数锚帧。
矩形均含整数 x/y/width/height，拒布尔值、越界、未知字段和变化源。
区域按居中 contain 映射到视口，保留浮点边缘；边缘恰好相接不计相交。
这不模拟 cover、手势、设备像素密度或真正的平台布局。

每张图明确标 `SIMULATION / NOT PHONE APPROVAL`；遮挡块为原创红色示意，
不是复制平台界面。每次单帧本地 FFmpeg 解码有 60 秒超时，并校验 RGB 字节数，
不缓存整段原始视频。依赖现有 Pillow/FFmpeg，不安装或联网。输出新私有目录 0700，
拒覆盖、符号链接祖先与路径穿越；源哈希结束时复查。失败目录保留，不写成功 result。
结果同时声明 operator_declarations_only、actual_phone_test=false、
automatic_readability_or_privacy_approval=false。未声明内容、不同真实布局和人的感知仍不受此检查保证。

真实案例、许可复核、全帧扫描与数值检查边界见[平台与媒体检查记录](creative-platform-validation.md)。
