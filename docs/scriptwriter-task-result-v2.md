# 编剧任务与成果契约 v2

2026-11-04。用户反馈旧Gamma片虽能解码、字幕不超时，但机械翻页配制作流程检查语，无法解释实际价值。后来的12秒版逐句匹配页面，仍只是页面介绍。本次升级助手编剧Skill和标准库校验器，目标是让真实成果、具体任务与实际操作形成故事，素材不支持时交出缺镜清单。

## 方案来源与review

按用户偏好实际调用本机Claude Code2.1.310，沿用已授权供应商；safe-mode、空工具/空MCP、禁hook、禁浏览器连接和会话持久化。90秒上限后终止并回收进程，退出143，stdout/stderr均0，没有响应模型或可用方案。没有冒称Claude设计，也未重启长调用；已发送请求的服务端执行/计费无法由本机退出证明取消。

本次方案、独立契约review与代码由Codex完成。Review限定为可复用编剧契约，不实现模型服务、权限网关或剪映私有草稿注入：

- 结构检查与创作分工不变。情绪点、可见变化和钩子是待审创作说明，不是“有趣评分”或自动语义证明。
- 不将成果页当成制作过程。结果展示、真实输入、操作、修改及核对分别注明证据类型，不能通过把台词标成opinion规避缺镜。
- 来源声明与独立test_report继续分离。媒体哈希、区间和人工观察只建立可追溯关系，不自动证明工具成功或授权。
- 缺素材仍可导出待审方案，但明确needs_materials，不输出可执行剪辑计划。旧版本保留、显式标legacy_unassessed。

## 新草案

使用整数`schema_version=2`。原candidate/brief/assets/recording_steps及来源、实测归因规则保留。新字段严格拒绝未知键；布尔不是整数。完整待拍示例为[53秒Gamma任务草案](../fixtures/scriptwriter/gamma-task-result-draft-v2.json)，其中八镜全部明确缺素材，不是实测报告或成片脚本。

`story`含：

- `format`: task_result或footage_preview。后者仅用于如实标注的页面/素材预览，不冒充完整工具测试。
- `hook_alternatives`: 2–3个不同钩子；`chosen_hook`必须是其中一个，并与brief.hook相同。

每场保留visual/narration/subtitle/purpose及证据ID，新增：

| 字段 | 内容 |
| --- | --- |
| beat | result/task/input/action/change/limitation/audience |
| goal | 这镜具体要回答什么 |
| hook | 本镜为什么值得继续看，不能预设未知结果 |
| visible_change | 观众能实际看到的前后变化 |
| emotion | 一处生活化观察或情绪，不制造假事实 |
| transition_purpose | 为什么在这里切镜，下一镜要解决什么 |
| fallback | 缺镜或证据失效时如何转移、补拍、改稿或删减 |
| lines | 逐句旁白、字幕、目标时间和真实素材绑定 |

首镜必须为result；未取得真实结果时，该句写missing。task_result覆盖七种beat，未规划的beat也进入缺项。建议53秒示例节奏：成果0–3、任务3–8、输入8–14、关键选择14–20、实际成品变化20–32、修改换主题32–42、事实及限制42–50、适用人群50–53。不能为符合节奏重复冻结画面或伪造操作。

每句字段为id/start_ms/end_ms/narration/subtitle/status/evidence/missing。全片line ID唯一，目标区间为整数毫秒半开区间，必须无缝覆盖本场。场级narration/subtitle分别与各句用换行拼接的文本完全一致，拒绝未绑定台词。语言切句和真实语义仍由作者审核。

available句必须有1–4项evidence、missing=null；每项为media_id/segment_id/in_ms/out_ms，区间须落在对应已观察源片段内，素材资产须属于本场。missing句必须无evidence，明确reason、required_shot及next_action（capture/transfer/rewrite/omit）。不能以空字符串、未知证据ID或注释“之后再补”冒充已取得画面。

## 媒体目录

新增`--media-catalog`，仅v2可用。目录示意：

```json
{
  "schema_version": 1,
  "media": [{
    "id": "recording-1", "asset_id": "screen-recording",
    "path": "sources/original.mp4", "sha256": "<64 lowercase hex>",
    "reviewer": "Actual operator attribution", "observed_at": "<actual timezone-aware timestamp>",
    "segments": [{
      "id": "cover", "in_ms": 4500, "out_ms": 10000,
      "kind": "screen_result", "observation": "Actual visible fictional title page",
      "test_ids": []
    }]
  }]
}
```

以上占位符需换成真实值；空目录`{"schema_version":1,"media":[]}`可用于明确待拍方案。最多32个本地视频，每个1–128个已观察区间；ffprobe单项超时15秒，只探测流/时长，不录制、不生成视频、不改原件。要求单一视频流及可靠的视频流时长，不使用可能包含较长音轨的容器时长；多视频流或视频时长缺失（例如某些WebM）会拒绝，尚未实现PTS全扫描后备。源码哈希以流式SHA-256重算，探测前后核对文件身份/大小/修改时间，变化即停止；相对路径限evidence-root，禁止URL、绝对路径、`..`及内部软链接。不是针对同UID恶意进程的隔离保证。目录资产须为available_unverified：文件存在不表示权利或隐私已获批准。

区间种类为screen_result、context、tool_input、tool_action、tool_result、tool_change、tool_verification。tool_*还须引用独立test_report中已校验的观察ID；失败观察可用于实际限制，不自动升级为成功。test_result台词的画面须引用该镜至少一个相同的实测ID。操作者仍须检查测试文件、原录屏和台词之间的实际含义，ID关联本身不能证明一致。

task_result的result/input/action/change/limitation分别要求tool_result/tool_input/tool_action/tool_change/tool_verification。footage_preview也不能用screen_result顶替input/action/change/limitation。即使claim_kind=opinion也执行此规则。工具种类来自可信操作者注释，程序不自动判断哪一帧是生成、修改或错误。

源区间毫秒不是产品实际生成耗时；不能由剪辑片长反推速度。它也不是editing_agent的帧坐标：VFR/CFR、裁剪派生关系和字幕时序必须另行核对，尚无自动跨协议适配器。

## 输出与后续岗位

```sh
python3.12 -m scriptwriter_agent \
  --candidates <candidates.json> --tool-id <tool-id> \
  --draft <draft-v2.json> --media-catalog <catalog.json> \
  --evidence-root <private-evidence-root> --output <new-attempt>
```

原六件内容及manifest保留，v2多输出material-gaps.md；manifest覆盖七件内容。所有文件仍标为草案、human review pending、execution/publication_authorized=false。CLI退出0仅表示导出待审方案，包括有缺镜的方案。

`story_readiness`：

- needs_materials：任何句缺镜或任务beat未规划。不得冻结剪辑；列明转移、补拍、改稿或删除。
- evidence_linked_requires_review：本地身份、结构与区间通过，仍须逐句语义、权利、隐私和媒体审查；不等于可以录制或发布。
- legacy_unassessed：旧v1无媒体覆盖检查，兼容导出但不自动ready。

连到剪辑前，助手读取缺镜清单、实际原片和独立test_report，再显式映射editing_agent的visual_alignment；有缺镜不应构造“已完成”分镜。导出后还须原生工程重开、准确帧数、完整解码、逐句画面、连续观看、听审及用户验收，不能继承旧片批准。

## 初次实现时的素材与执行边界

以下是初次实现提交时的历史阻塞。后来已取得82秒源片并完成49秒剪映试剪，详情见[Gamma任务视频v4验证](gamma-task-result-validation-v4.md)；不能将旧“等待素材”状态当成当前状态。

Mac已核对的Gamma素材是约24.49秒的虚构星屿轻记演示页预览，能支持封面、四步工作法、学生虚构示例和部分翻页。缺少同一周报任务的真实输入、大纲选择、生成过程、修改、换主题和逐项核对；不能由这些素材剪出完整45–55秒工具实测。

另找到一个名为gamma-feature-demo.mp4的Library候选，但当前下载helper报告`Library prepare_materialize is not available`，未取得字节或确认内容。没有绕开规定传输路径，没有重录、登录、生成或消耗点数。该候选需由统筹提供可用的授权转移及实际观察说明后，再判断能否使用。现有素材身份、哈希及缺镜盘点留在本机，不提交私人媒体或Library资产ID。

新Gamma成片在这一步尚未编辑、导出或上传。此项等待素材，不把结构/离线测试写成真实成片成功。

## 初次实现的本机验证

最终选择性回归98项通过，3.258秒：编剧60项（原35项加新增25项）及剪辑38项。覆盖旧v1兼容、素材缺项导出、钩子/首镜/故事beat、逐句无缝覆盖与未绑定文字、严格字段/版本/毫秒类型、引用和实际视频范围、哈希变更、探测中被改写、路径/软链、未来时间、操作与预览分离、独立实测关联、JSON重复键/非有限数、输出manifest及不可覆盖。长音轨不扩大视频区间，多视频流/未知视频流时长拒绝。没有运行桌面输入或录屏；未改动的录屏/AVFoundation测试本轮未重跑，不称全仓全绿。

另用已核对的真实Gamma CFR导入派生片运行CLI：单视频流24500毫秒、哈希匹配，三个实际页面区间通过目录检查，状态仅evidence_linked_requires_review。它是文件契约验证，不是用户要求的新实测片。53秒任务草案导出needs_materials、八项缺镜、实测记录0、执行与发布授权均false。把这段翻页片当task_result时CLI退出2、不创建package；改媒体哈希同样退出2、不创建package。

compileall、CLI帮助、链接与差异格式检查通过。原片和已有剪映项目未编辑；没有新增视频、上传或发布。
