# 单窗口录屏 MVP 工程验证

日期：2026-11-02 UTC。记录与实现作者：Codex。基线 main `289686fca6422c064f2bbfc0af1de93a5b7ad880`，工作分支 `work/recording-agent-mvp`；本记录是新helper实际验证，不把此前临时探针结果冒充本版通过。父助手已复核身份修订、77项测试、实际媒体及幂等结果并通过最终review，发布范围仅该工作分支，不合并录屏代码到 main。

## 修订与测试

- 本机 SDK 明确 NSRunningApplication.launchDate 对未经LaunchServices启动的程序可能不可用；实际诊断也观察到fixture当前PID等于owner PID、窗口ID相同、running application存在但launchDate为空。
- fixture 改为当前进程及其自建/保留NSWindow的严格身份策略，窗口可见性、权限、父进程、几何、前台和显示器守卫均保留；不伪造日期。第三方picker仍要求bundle/PID/可靠启动日期，无法核实即拒绝。
- 精确窗口查询改为传入原始CGWindowID值的单元素CFArray，修复NSNumber对象数组造成查不到目标的问题；没有扩大枚举或换成全屏。
- 已有0755/0777 root直接拒绝，0700接受，不擅自chmod；状态/配置/日志/manifest私有创建，原子JSON独占发布，symlink或既有冲突对象不覆盖。仅是同UID合作式模型，不防御同UID恶意进程。
- 无stop的完成时长要求目标±0.75秒（待校准假设）；异常过短即隔离。持久stop请求与后端requested意图同时存在才允许早停有效媒体，标partial/stopped_early、plan_complete=false，不能通过完整拍摄计划。
- 77项unittest全通过：35项既有发现测试、42项录屏相关测试。其中实际编译并运行纯Foundation Swift策略的14个断言：空launchDate但严格自身身份允许；错误owner/window、不可见窗口、缺失或变化的外部启动身份拒绝；CGWindowID数组表示正确。该策略测试没有GUI或捕获调用。
- 当前完整Swift后端重新编译exit0；合并源码构建哈希为 `e56838b32fa6be2d5953f60e86e564407f5fc8f71afea1f70389ef80a38a11d3`。没有安装依赖或调用模型。

## 尝试与实际开录次数

| take | 结果 | 是否开录/产物 |
| --- | --- | --- |
| fixture-001 | 旧自身身份要求导致invalid_selected_identity，exit2 | start前终结，无MP4，保留 |
| fixture-002 | 已观察到自身launchDate为空；窗口数组表示错误导致target_window_disappeared，exit2 | start前终结，无MP4，保留 |
| fixture-003 | 修订后同步CLI正常结束，exit0，validated | 唯一一次recording_started，完成原片和manifest |

旧尝试均核对了终结事件、没有start/MP4后才创建新request/take。未覆盖失败诊断，也未因未知结果重复开录。新后端屏幕录制与AX预检均True，没有调用授权申请API、打开设置或更改TCC/SIP。

## fixture-003 媒体与源身份

录制窗口只含程序生成的测试文字、计数与色块，选择模式固定fixture，SCShareableContent限定本进程，SCContentFilter为desktopIndependentWindow。实际owner PID等于当前PID，SCWindow ID等于保留NSWindow ID，launchDate真实为空；manifest使用current_pid_and_retained_nswindow证明且日期为null。没有picker、第三方窗口、Gamma或任何账号访问。

| 检查 | 实际结果 |
| --- | --- |
| CLI/后端 | exit0；完整start、stop、finish及退出事件；未检测到前台/源干扰 |
| 实际录制时段 | 2026-11-02T15:51:30Z至15:51:37Z（回调UTC） |
| 计划 | 7秒、容差±0.75秒；未显式早停；plan_complete=true |
| 文件时长 | 7.363333秒 |
| 尺寸/源布局 | 1440×696px；720×348point、2x |
| 编码/帧 | H.264、112帧、平均16800/1111fps（VFR，不宣称30fps） |
| PTS | 112个严格递增展示时间，首0.0秒、末7.331667秒 |
| 声音/摄像头 | 零音轨；系统声/麦克风false，无摄像头调用 |
| 媒体验证 | 真实ffprobe + 完整FFmpeg解码exit0、零错误；独立ffprobe再次核对单视频流 |
| 文件字节 | 561,447 |
| SHA-256 | `56f10d1c848286fa319c0ba0abb5330635dd732ca96d8b9afc93b3912670027a` |

原片及manifest完成后才写validated终态；检查root/take目录0700，原片、manifest、配置、事件、诊断日志和SQLite为0600。原片、抽帧、日志与真实目录均未入库，未发送至任何模型服务。本记录仅包含原创工程摘要和哈希。

## 幂等与限制

实际CLI终态status查询两次、同request_id的stop两次，四次均返回同一个validated/plan complete结果；原片和manifest的hash、mtime、字节均未改变，也未创建stop控制文件或启动新后端。真实在途早停尚未实跑，其partial策略由离线用例覆盖。

tool_test_status仍为not_performed，视觉审看仍pending；这次只证明自建安全窗口的录屏/媒体链路。没有整片逐帧语义/隐私审看，不凭抽帧或解码证明全部画面安全。第三方picker、真实产品操作/输出、系统声音、稳定30fps、长期录制、OS隔离/epoch fencing、业务SQL集成和无人值守仍待独立验收。PTS与callback只作近似锚点，不冒充逐帧精确host-clock映射。
