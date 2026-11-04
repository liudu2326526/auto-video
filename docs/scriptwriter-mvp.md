# 编剧 Agent MVP

日期：2026-11-03。实现形态：**助手编剧 + 仓库Skill + Python标准库校验/导出器**。不是后台LLM服务，不声称端到端视频系统已经完成。

## Claude Code 方案与 review

本次实际调用已安装的 Claude Code 2.1.316，使用现有已授权供应商配置，`-p --tools '' --max-turns 1` 仅请求设计。调用成功退出0，产出12,856字节方案；未让Claude写代码或读取凭证。完整原始方案保留于忽略的运行目录；文档不包含密钥或供应商配置。代码、独立review、测试与Gamma示例由助手实施。

采纳方案中的：创作与确定性检查分开、证据归因、输入指纹、时间轴/引用校验、计划产物、人工审核、实测回填、原子导出与manifest。

Review后修正/缩减：

- 不采纳“observed比厂商声明强，因此可成为产品实测”的简单阶梯。发现层observed仍可能只是网页观察。真实实测使用独立test_report，核对本地输入和结果文件哈希。
- Claude示例的候选外层和capability_tested位置与仓库不同，实施使用现有候选数组及assessment，不迁移上游。
- 不把“文字没人读”“生成六页”等情节预设为实际结果。Gamma开头提出问题，不用假结果作钩子。
- 不实现自动final升级或以签名字符串当权限。所有输出固定为draft_requires_human_review，执行与发布权限固定false。
- 不声称正则能理解事实、识别人名、验证权利。只拦常见夸大/未归因结果话术；完整语义、隐私、素材权利与证据观看仍人工检查。
- 不硬性要求240–300字；字数只作语速警告，真实朗读/音频时长后验。

## 运行

要求Python 3.10+，无第三方依赖、网络或API调用。

```sh
python -m scriptwriter_agent --help
python -m scriptwriter_agent \
  --candidates var/discovery/runs/aihot-gamma-20261102-02/candidates.json \
  --tool-id tool_c349fe616c0a50d06288 \
  --draft fixtures/scriptwriter/gamma-draft.json \
  --output var/scriptwriter/gamma-new-attempt
```

Gamma命令依赖本机已有的真实发现产物；干净clone不会含忽略的var目录。可先通过发现Agent生成候选，再由编剧基于新候选建立draft。离线测试自建明确标为fixture的候选，不将测试fixture冒充真实Gamma来源。来源过期时必须重新核查，不能改时间绕过。

CLI：成功导出待审草案退出0；无效输入/IO错误退出2。没有 `--final`、模型API、浏览器、录屏或发布命令。输出目录必须不存在，每次新建attempt。写入临时目录后改名，失败清理临时文件；单写者MVP，不提供分布式锁或同路径并发写保证。

## 输入契约

### candidate

复用discovery的单个候选：tool_id、name、assessment.facts及gates。facts中的citation含https URL、sha256、quote及expires_at。helper核对引用结构及时效，**不会重新抓取网页或验证quote确实属于该哈希**；真实性继承可信研究者的已审来源。

### draft（schema_version=1）

完整例子见[Gamma草案](../fixtures/scriptwriter/gamma-draft.json)。

- tool_id与candidate一致；candidate_digest是候选规范JSON的SHA-256。候选变化必须逐条重审引用，不能只更新digest。
- brief：audience、pain_point、angle、hook、payoff、demo_input、success_criteria、15–180的整数duration_seconds、input_classification=synthetic_nonpersonal。
- scenes：3–16段；唯一id、连续整数start/end、visual、narration、subtitle、purpose、asset_ids、source_ids、test_ids、claim_kind。
- claim_kind：question/instruction/source_claim/test_result/opinion。source_claim需source_ids及attribution=来源声明，非本项目实测；test_result需test_ids及attribution=本次实测记录，非普遍效果保证。不能把结论伪装成opinion或问句逃过语义审核。
- source IDs为候选facts顺序对应source_001等；整个候选摘要绑定防止重排误指。untested不能作为断言证据。
- assets：唯一id、description、origin、rights_review、privacy_review、status（to_capture/to_create/available_unverified）。helper不将素材标为已审核。
- recording_steps：唯一id、action、expected_evidence、stop_condition、recovery、scene_ids，必须覆盖所有镜头。

### 可选 test_report

schema_version=1、tool_id一致、observations数组。每项含唯一id、status（passed/failed）、claim、scenario、limitations、reviewer、observed_at、expires_at，以及input_artifact/result_artifact（path与sha256）。path必须位于指定evidence_root内，不能绝对路径或通过软链接逃逸。已存在的真实输入/结果字节必须匹配摘要。

观察日期不能在未来，也不能过期。记录失败是正常结果，不能被改写为成功。测试文件哈希不自动证明旁白与文件内容一致；必须由人工核对claim、情境、原片与结果。MVP不完整解码媒体、不验证录屏时间点，也不提供受信签名或防恶意本地伪造。

## 交付物与范围

- package.json：全部结构化内容、原始来源事实与引文、实测记录、输入digest、状态与阻断项。
- script.md：逐段旁白、字幕、证据ID及归因。
- storyboard.md：时间、画面、目的、素材映射。
- assets.md：待拍/待建素材及许可、隐私检查。
- recording-plan.md：固定输入、成功标准、操作步骤、证据、停止/恢复条件。
- review.md：待办检查、门槛、警告和pending结论。
- manifest.json：前六件文件的SHA-256。

每个Markdown文件明确标为草案。来源引用正文转义，内容不执行；JSON内保留原始资料供审核，不应被下游作为指令。源资料必须视为不可信。输入demo_input分类来自可信作者声明，程序不能自动证明不存在个人信息。

所有产物固定execution_authorized=false/publication_authorized=false。发现门槛未通过仍允许制作计划，但输出阻断项，不能由这个产物启动录屏。完整状态机、自动选题、持续运行、TTS、编辑器与发布不在此MVP内。

## Gamma真实运行（非产品实测）

实际消费2026-11-02发现run的Gamma候选：3项来源事实、7项门槛unknown、capability_tested=false。2026-11-03成功导出60秒、6段、3类素材、5个录屏步骤的计划。只引用官网制作演示文稿的声明；不使用未实测的免费额度/速度断言。

固定虚构“纸飞机演示项目”周报：注册120、激活72、访谈8人、修复12项。目标是6页中文汇报，检查结构、数字、不编造和修改保存；不是宣布Gamma已经通过这些检查。示例可作为重新录制的执行清单，但需先处理账号、额度、录屏用途等门槛。没有登录Gamma、生成演示、录屏或发布。

测试覆盖见tests/test_scriptwriter.py，全部是离线程序校验，不构成Gamma账号内验证。

## 本次验证结果

- 68项离线单元测试通过（原发现层35项 + 编剧层33项）。覆盖来源/实测分离、绑定候选摘要、过期来源、丢失或伪造哈希、软链接/路径逃逸、未来/过期观察、时间轴、悬空引用、输出不可覆盖、故障清理、转义及CLI。
- 原有23项架构契约fixture、16项发现设计检查通过。这些是文档/fixture检查，不是完整生产系统验收。
- compileall、git diff --check、本页相对链接检查通过。
- Gamma真实候选→拍摄草案导出通过；Gamma产品功能、账号、录屏、生成结果和完整视频仍未验证。
