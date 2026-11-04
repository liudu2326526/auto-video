# 实测执行 Agent：真实 GUI 验证

2026-11-04，Codex 工程事实记录。主任务确认剪映实测结束、桌面释放后执行。本次复用 Claude 已生成并由 Codex review 的执行方案；没有另冒称 Claude 设计了界面场景。局部状态分类修订及代码由 Codex 完成。

## 实际运行范围

系统自带 TextEdit，bundle ID `com.apple.TextEdit`，本机 Info.plist 实际读到版本 **1.20（409）**。输入为原创 `hello 星屿` 加换行，两份独立工作文件保存在 Git 外 0700 私有证据目录。脚本/输入先由 execution_agent plan 冻结，run 返回 operation ID/token 和 waiting_observation 后才操作。

使用项目 execute-tool-test Skill 和受支持的 CUA：按步骤读取指令、打开指定文件、核对 AX 中的完整本地 URL/标题及内容、菜单点击/粘贴/保存、读取界面和实际文件、observe 回填、status/handoff 验收。不是 CLI 自行驱动 GUI，也不是无人值守通用软件自动化。

没有打开浏览器/网站、账号或旧文档，没有上传文件、操作剪映/Gamma、调用 shell UI 自动化、修改系统权限、开启声音或启动录屏。CUA 的单应用截图只显示原创文件标题及内容，未保存为本地 screenshot 证据工件；持久证据为实际文件和注明来源的操作日志，不能冒称有截图资产。

## 脚本与结果

实际冻结脚本已保留为可复用 [textedit-gui.json](../fixtures/execution-agent/textedit-gui.json)，输入与 [input.txt](../fixtures/execution-agent/input.txt) 相同。复测需先在新私有证据目录准备两份名为 AV-Test-Result-One.txt/AV-Test-Result-Two.txt 的工作副本，不可修改仓库 fixture 或运行根内的 frozen input/script 快照。确认本机 TextEdit 版本后再冻结新的 manifest；窗口身份必须按新目录完整 URL 核对，不能只看同名标题。

| 步骤 | 实际界面操作 | 实际产物 | 执行器验收 |
| --- | --- | --- | --- |
| uppercase | 打开 One，选全文，编辑→转换→变为大写，保存 | `HELLO 星屿` 加换行；界面及文件一致 | succeeded，result_verified=true |
| injected-mismatch | 打开 Two，按故障脚本粘贴 `HELLO 星屿 -- deliberate mismatch` 加换行，保存 | 故意与冻结成功判据不同 | failed，independent_result_mismatch |

第二项是明确声明的受控故障，不是 TextEdit 产品 bug。operator 回报“指定 GUI 动作完成”，执行器仍通过实际文件和事前冻结 expected 判失败；不会因保存成功或操作员 success 声明放行。测试总结果 **partial**。两个步骤均 `synthetic=true/test_kind=controlled_fixture`、GUI `process_ok=null`，没有宣称观察到某子进程退出 0。

没有观察到可归于 TextEdit 的产品限制，product_limits 保持空数组，价格未观察保持 unknown/null。不能把测试故障、CUA 超时或执行器 scope 限制写成 TextEdit 不支持某项能力。

## 真实时间与自动化故障

从受支持 CUA 调用返回值记录 `performance.now()` 时间，操作日志明示为 Codex 对实际工具结果的转录；不是独立平台签名审计，也不是纯产品处理时长：

| 观测 | 时间 |
| --- | ---: |
| 第一组五次 CUA 调用合计，含 AX 读取和自动化开销 | 5132.582 ms |
| 其中“变为大写→保存→AX复核”同次调用 | 613.364 ms |
| 第二组四次 CUA 调用合计，含同类开销 | 4838.866 ms |
| 其中“粘贴故意错误内容→保存→AX复核” | 824.318 ms |
| 第一 operation 从意图到 observe（含启动/协调/人工分析等待） | 183143.029 ms |
| 第二 operation 从意图到 observe（含等待） | 102928.624 ms |

`active_ui_elapsed_ms` 仍为 null，不能把 observation_wait 或 CUA 调用跨度当作 TextEdit 处理时间。

首次 `cua.getApp` 按完整 app 路径启动时，工具实际 41.3796 秒返回 timeoutReached；没有因此盲目重复编辑。用受支持 listApps 先确认 TextEdit 已启动，再按实际 bundle ID 绑定同一现有应用，2.1634 秒成功返回空打开对话框。之后只选择本任务文件，转换操作只执行一次。此证据没有证明“路径绑定必然比 bundle ID 慢”或推断缺权限；应用启动/首读与二次绑定时机不同。没有绕过拒绝、权限检查或使用其他 UI 通道。

## 证据与编剧回填

实际原件及运行目录只在本机私有 `execution-gui-20261104T031616Z` 证据目录，未提交或上传。

- One 文件 SHA256：`60e3a0b5bac3fe7b63be6f7cf25008916afb10517c7546897fdb08cf260891a1`
- Two 文件 SHA256：`56f84d6f494ae33cf656c11e9fbe0a707a5acc1844398836fcced6d3e8f312ce`
- handoff SHA256：`7d9e40feb7edeba23db83c8249d61803659ab8973ce5695af232958e2887a4f5`

两个 observe 将各自的实际结果文件和操作日志复制为私有绑定证据，operation/token 与对应 run/step 核验。handoff 再核验 frozen input/script 及所有证据哈希，包含 **1 条本测试验证事实、1 个失败 gap**，保留 synthetic、未知价格与录屏链接、`ready_for_script_freeze=false`，未调用编剧模型/发送消息或导入 SQL。结果哈希只证明字节；产品来源仍由操作日志断言，整体语义需独立审核。

实际重放第二个 observe 的同 request_id，结构化响应与原响应相等；所有步骤终结后再以新 request 调用 run，仍 partial，没有新待办或再次操作 GUI。两份原件哈希保持相同。

两份文件保存后，逐个核对并关闭自己的窗口；取消剩余打开面板并请求退出，后续 listApps 实际确认 TextEdit `isRunning=false`。本任务没有活动 UI 或录制进程，桌面已释放。没有终止其他应用。

## 为本次发现补充的代码与测试

- 新 manifest 可选 test_kind，省略保守分类 controlled_fixture；GUI fixture 不再误标成真实产品测试。product_test 只是声明，不自动解除来源/语义审核。旧原件/缓存 receipt 不重写。
- operator succeeded 且恰有一个明确错误结果文件：failed；缺失或多个结果文件：unknown。只有匹配冻结 expected 才 succeeded。
- 新增三项针对分类和错误 GUI 产物的测试；`python3.12 -m unittest discover -s tests -p test_execution_agent.py -v` 实际 **52 项通过，2.236 秒**。新版本 15 个交付文件的 Python 语法、manifest schema/JSON、本地链接、尾空格、常见凭据/个人路径扫描及 git diff --check 通过，暂存区为空。

剩余限制：尚无自动 CUA 调度器、可信外部动作批准/预算网关、UI unknown 自动对账、正式 SQL/录屏 take 导入或自动语义定稿。真实 Gamma/剪映业务结果、图形产品处理耗时及产品限制均未由本场景验证。现有 AVFoundation encoderNotFound 失败不在本次 GUI 测试中重跑；未关闭安全设置或请求更多权限。
