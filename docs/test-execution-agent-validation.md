# 实测执行 Agent：本机验证记录

2026-11-04，Codex 首轮离线工程事实记录。Claude 只产出初始方案，代码、review 与本记录由 Codex 完成；不是 Gamma/剪映实测报告。首轮没有操作桌面或启动录屏，遵守共享 Mac 的剪映优先时段。后续获协调时段的真实 TextEdit GUI 及补充测试另见 [GUI 验证](test-execution-agent-gui-validation.md)，不篡改本轮原始记录/哈希。

## 方案来源

本机 Claude Code 2.1.310，既有授权 muskapis；safe-mode、仅 user setting source、strict 空 MCP、无工具、禁 hooks、不持久化会话、关闭非必要流量、medium effort。调用有 90 秒上限，实际 52.987 秒，exit 0，result success/is_error=false，正文 5035 字节。配置模型 opus，供应商报告 `claude-opus-5-5[1m]`，不声称已独立验证供应商模型身份。

初稿 SHA256 `3c54b0102b3d996d0976c4f8da390f5ffa7be2e223c99489b3d09da7640effad`。仓库保存该初稿正文及 review；原始 stdout/运行记录仅本机证据目录，不入库。不读取/复制凭据，不发送原片、账号文件或私人内容。review 修正了初稿 unknown→blocked SQL 映射及 UI success 证据不足等问题，最终契约见实施文档。

## 自动测试

`python3.12 -m unittest discover -s tests -p test_execution_agent.py -v`：**49 项通过**，2.098 秒。本模块实际 subprocess worker 与 stdout CLI 均参与测试，不全是 mock。

覆盖：

- 正常独立产物验证、exit 0 但错误产物、非零退出、实际输入限制、混合 partial、真实有界超时 kill/wait。
- 同 request 重放/参数冲突、同 step 新 key 不重跑、已存在 run 不覆盖、intent 中断 unknown、跨 run 同动作 unresolved 阻断。
- 绑定 receipt 对账且不再执行、不匹配/缺 receipt 保留 unknown、进程 lease 继承而非 PID 推断、明确 spawn 失败单独分类。
- 0700 root/0600 内部文件、0755/0777 root 拒绝且不 chmod、链接/路径越界/Git 内输出拒绝、既有输出与 symlink 不覆盖、快照/证据篡改或缺失拒绝。
- 日志 16 KiB 截断与 retained/full-stream 哈希分别核对，任意 argv 与超界参数拒绝。
- GUI 只测观察回填契约：不启动桌面、token 匹配、toast 无产物为 unknown、匹配文件仍保留来源/语义限制、取消、重复回填、时钟异常不伪造耗时、限制来源归属。
- handoff synthetic/不能定稿、未知价格/录屏绑定、未知状态不可产生 SQL success、未来 schema 拒绝。
- 证据同时绑定其 test_run 和 operation，不能把另一操作的真实产物移接为本步骤证据。

已有工具发现模块 35 项离线测试通过（0.117 秒）。已有录屏/浏览器模块 77 项回归中 72 通过、5 失败（19.604 秒）：

```
test_authoritative_generated_frames_mux_and_decode
test_budget_rejection_never_writes
test_external_non_atomic_evidence_never_writes
test_geometry_change_discards_already_encoded_segments
test_wrong_dimensions_never_writes
```

五项均在既有 `test_recording_crop.CropMemoryTests` 初始化时返回 `writer_start_failed:AVFoundationErrorDomain:-11834`，尚未进入各自主要断言。本轮没有改该代码；当前证据只确认 writer 初始化失败，未证明其底层原因，也未修改测试跳过、放宽阈值、触发屏幕权限或改安全设置。不能宣称全仓回归全绿。并行剪辑模块仍在工作，不运行/改写其 UI 验证或源码。

Skill 通过 skill-creator initializer 创建。原 short_description 太短，已修正；元数据生成器 `--name execute-tool-test` 正常完成。Python `quick_validate.py` 因当前 Python 3.12 无 PyYAML 退出 1，未安装依赖。使用本机 Ruby `YAML.safe_load` 校验 frontmatter 和 openai.yaml，并检查 name/description、25–64 字符短描述和 `$execute-tool-test` 默认提示，全部通过；不冒称 Python 校验器成功。

本次 13 个新增交付文件的 Python 语法、fixture JSON、本地链接、尾空格、常见凭据格式及个人绝对路径扫描通过，`git diff --check` 通过。文件仅源码/文本，无媒体、运行日志、数据库、编译二进制或本机配置。

## 真实 CLI 的受控场景

使用仓库 `fixtures/execution-agent/mixed.json`，input 为原创 `hello 星屿` 文本。运行根在仓库外私有证据目录；依次实际执行 plan/run/status/handoff，四个 CLI 均 exit 0，run 与 status 结果 partial。

| 步骤 | 实际状态 | process_ok | result_verified | executor_elapsed_ms | worker_elapsed_ms |
| --- | --- | --- | --- | ---: | ---: |
| uppercase | succeeded | true | true | 74.269 | 0.424 |
| wrong-output | failed | true | false | 39.613 | 0.402 |
| input-limit | blocked | false | false | 72.614 | 0.175 |

第三步实际观察到 `max_input_bytes=1/source=adapter_observed`，不能表述成 Gamma/剪映产品限制。故障值是明确的 fixture 注入，不是假称真实产品失败。worker 时间只覆盖其处理区间；executor 时间包含启动/等待/日志开销，不是图形产品生成时间。

handoff 有 1 条验证产物事实、2 个 gaps 和限制；该事实 synthetic=true，`ready_for_script_freeze=false`，价格未知，录屏链接未导入，未发送给外部编剧服务。handoff 文件 SHA256：

`0236dad890b64b7577d69b0786e94ab4469a98b29521a5ca0fa8c56c96dcdad8`

本次 demo 的 `run` stdout SHA256：`d572dff6aefbfdc2a861aee8a88f6b02195d2fee817b9c627f371a49bb4eaf24`。所有子进程正常终结，后续 status/handoff 成功取得根锁，没有活动桌面/录屏 lease。

## 剩余范围

此首轮检查点时，GUI 实测执行、真实产品限制和 active UI 耗时、第三方来源与语义验证、录屏 take→test 自动对接、正式 SQL 导入、可信外部动作批准/预算对账没有实现或未实测。后续已完成 TextEdit GUI 成功/故意失败的受控场景，其余限制仍保留。监督桥接不是无人值守 GUI 自动化。

本次新增源码/测试/Skill/fixtures/文档留在现有工作树，不 stage、commit、push 或合并；既有未提交录屏变更和并行剪辑文件保留。原始 Claude 运行日志、SQLite、输入快照、worker 日志/产物只在私有证据目录，不加入仓库。
