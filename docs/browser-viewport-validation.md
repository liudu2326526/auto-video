# 浏览器原生视口视频：Claude草稿、review与本机验证

2026-11-03。此为fixture实验，不是已接入recording_agent的第三方浏览器录制后端。

## 来源与实际Claude结果

用户明确要求再试一次Claude，不成功时允许Codex继续。本次仅发送获准的无秘密方案事实到
现有api.muskapis.com，safe-mode、user setting source、空MCP、无工具、无hooks、无持久会话。
60秒上限，35.993秒退出0，result success、is_error=false；供应商报告模型
`claude-opus-5-5[1m]`，配置别名opus。模型路由身份来自供应商报告，非独立鉴定。
未发送媒体或私人数据；没有修改本机认证或持久设置。前两次超时记录保留，不改写其计费未知边界。

以下为本次Claude实际输出，初稿SHA256：`9885bb7b2bdaf29c698095ff347907cbe2055c90afe7e59320cca00e8836d300`。

### Claude草稿原文

**结论**：该路径可做受限验证，但只能主张"源类型边界 + 失败即丢弃"，不能主张"无泄漏"。

**竞态**
1. 授权→首帧→start 之间 track 可变。需在 `onconfigurationchange` / `onended` / `onmute` 重校验 displaySurface、宽高、帧率，不符即停并清空 RAM。
2. Capture Handle 随导航变化，须监听 `oncapturehandlechange` 并每次重比 origin，不能只在录前校验一次。
3. 52ms 变尺寸说明初始 settings 不可信。改为编码前稳定窗：连续 ≥200ms 宽高不变且与预期视口一致才开录。此仅证时间稳定，不证像素来源。

**边界**
可证明：选择器授予、`displaySurface==="browser"`、`handle.origin` 同步可读。不可证明：帧内无某 UI 像素——需逐帧分析，而异步通知滞后、存在窗口期。故事件是检测手段，不是安全边界；边界应是编码后置校验 + 默认丢弃。

**Gamma**
是否暴露 Capture Handle 未知，不得假定。handle 为空或 origin 不符时，即便 displaySurface 为 browser 也只留 RAM、不落盘；仅当能自建 fixture 并显式设置 handle 时才走此路，否则拒绝。

**最小验证**
单页 fixture：固定色块 + 帧号 + 已知视口，录 6s；验零音轨、无 handle 变化、尺寸稳定、≤16MiB；再抽帧检查四角与顶边像素有无标签栏/提示残留。

**拒绝条件**
displaySurface≠browser；存在音轨；handle 空或 origin 不符；稳定窗内尺寸变化；track ended/mute；超 6s 或 16MiB；任何需扩权、绕过选择器或换源的路径。

## Codex review与实施决定

- 采用真实browser surface、临时选择器授权、默认无声、精确origin+handle匹配、失败清空、
  编码前有限尺寸稳定检查。origin相同不足以证明是目标文档，不能只校验origin或标题。
- 不假定`configurationchange`可移植或通知与帧原子对应；已查W3C Media Capture接口/事件定义，
  不能以一个监听器名字作为安全保证。现有实现使用ended/mute/capturehandlechange及有界检查。
- 不采纳“抽帧证明无泄漏”。browser surface由浏览器接口定义为浏览上下文渲染内容，排除浏览器栏
  的依据是源类型边界；OCR和标记检查只是本次受控fixture验证，不能取代任意页面的隐私审看。
- 稳定观测窗只是处理启动时临时设置，不证明所有瞬态不存在。跨导航/身份异步事件、短暂尺寸变化
  仍存在可见性限制；本实验不能据此放行第三方私人页。Gamma未验证Capture Handle，不发起录制。
- 代码由Codex实施，Claude提供上面的设计审查。`examples/browser-viewport-probe`与正式CLI分离；
  正式Chrome整窗路径仍在建流前拒绝，无静默回退。

## 已完成真实视频验证

通过Chrome正常标签页选择器，仅选自建带身份标记的fixture。设置audio:false、排除整屏和换源入口，
未隐藏标签栏、地址栏、书签栏或浏览器调试提示，没有新增扩展/系统权限。
使用现有同UID桌面互斥锁；自建页放入独立副屏窗口，未移动用户的Gamma或其他标签页。

- 文件：本机证据目录的`fixture-stable/fixture.webm`，原片不入Git或模型服务。
- SHA256：`791d2e8a4e9d6db5768448f3dd256106e9465f1a3071e9bcdbe75e048b358556`。
- 1750×958，VP9，442206字节，176帧，一个视频流、零音轨，完整FFmpeg解码退出0。
- 目标6秒；PTS从0到6.471秒，跨度在实验±0.75秒假设内。WebM头无format.duration，
  末帧持续时间未提供，因此6.471是PTS跨度，不声称精确容器时长。
- 观测平均27.0437fps；track settings 30fps，r_frame_rate 60/1均不能当作固定30fps验收。
- 浏览器记录stop_intent 6001.7ms、saved 6012.8ms；约0.46秒启动缓冲/预卷包含于媒体PTS，
  不裁切历史原片或篡改时间。第一样本clock在Broadcast start前，后续样本clock正常重置推进。
- 对所有176帧逐帧检查四角色块，共704处均通过，网页四角没有误裁。
- 四个本机Vision样本(0.3/2.8/4.8/6.2秒)均有四角标记与CONTENT ONLY；clock/Phase变化，
  未检出地址/调试提示等检查词。页面内部布局在第2/4秒自动变化。
- 这不是完整人工视觉验收，也不是浏览器工具栏变化、鼠标或其他浏览器兼容性验证。

## 当前阻塞、清理与待验

准备追加身份撤销负例（自建页编码2.2秒后改Capture Handle），尚未点击录制按钮。
仅创建/移动本任务标签页的窗口准备组合调用33.354秒超时；精确查询测试标签2030173243
随后返回`Browser is not available: 3`。该服务只有ready报告、无prompt/start事件、无媒体，
不能推测负例已运行。没有重复启动捕获。

上次遗留的2030173218/2030173221已逐一核对并关闭；成功片对应2030173226/2030173229也已关闭。
最后准备负例的两个新标签2030173240/2030173243关闭状态不确定，需浏览器重新连接后只核查这两个。
三个本轮本机服务均已停止（Ctrl-C退出130），桌面锁已实测释放，无捕获在运行。

尚待：真实身份撤销/尺寸变化负例、预算/取消/晚授权测试、动态浏览器缩放/工具栏及鼠标、
正式manifest与CLI接线、Gamma精确目标身份、Safari/Firefox/Edge兼容性和人工审看。
本轮新增JS/Python语法检查与diff检查通过；没有把前阶段100项测试当作新增功能测试通过。

官方资料：
[Screen Capture](https://www.w3.org/TR/screen-capture/)、
[Chrome controls](https://developer.chrome.com/docs/web-platform/screen-sharing-controls)、
[Capture Handle](https://developer.chrome.com/docs/web-platform/capture-handle)、
[Media Capture接口](https://www.w3.org/TR/mediacapture-streams/)。


## 原生CUA续测：连接分层、清理与保护负例

Chrome扩展目录仍列出id3，但精确读取测试标签调用10.7336秒超时；这不是录屏权限拒绝。
原生`cua.getApp(com.google.Chrome)`在2.9032秒返回，正常Chrome按钮、快捷键和选择器均可操作。
后续只使用受支持的原生CUA界面，没有用CDP、AppleScript或系统权限修改绕过故障。
旧测试窗口的AX明确显示两枚本任务标签及ready状态，已关闭该独立窗口；之前不确定的清理已闭环。

初次原生续测未取得2秒内的稳定尺寸，在编码前退出，无媒体。这只证明保护生效，不能证明
原因为后台节流。随后依据[Chrome Conditional Focus](https://developer.chrome.com/docs/web-platform/conditional-focus)
在正常选择器授权时保持录像页前台，并添加采样观测：真实采样显示visible，约101ms间隔，
从临时1920×1080变为1750×1028后，在约606ms取得5次连续相同尺寸。
该改变控制焦点，不隐藏授权/调试提示，也不修改浏览器安全设置。

以下是实际浏览器流的负例，不是模拟MediaRecorder或截图合成：

| 场景 | 实际终态与证据 | 落盘 |
| --- | --- | --- |
| 启动尺寸不稳定 | source_size_did_not_settle，编码前拒绝 | 无媒体 |
| 录制中尺寸变化 | 约1451.9ms，1750×1028→1836×1080；丢弃5段117283字节 | 无媒体、retained=0 |
| 精确handle变化 | 自建fixture在700ms注入不同handle，约702.5ms拒绝；丢弃2段58821字节 | 无媒体、retained=0 |
| 编码预算 | 故障配置64字节，在首段约213.2ms拒绝；无已保留段 | 无媒体、retained=0 |
| 超时后迟到授权 | 故障配置选择期限1500ms；正常选择器稍后分享，late_grant_stopped=true，无recording_started | 无媒体、retained=0 |

首个原定2.2秒handle撤销被更早的真实流尺寸变化打断，原记录保留；没有把它冒充handle测试通过。
为隔离原因，另建证据目录在700ms注入handle变化，才独立通过身份撤销路径。
尺寸负例是浏览器实际报告的流重采样/尺寸变化，未证明其根因，也不能冒充人工拖动窗口测试。
64字节与1500ms为故障注入参数，不是把默认16MiB和40秒限制改宽；默认值保持不变。
录制前事件目前elapsed_ms为0，故迟到授权只验证顺序与停止结果，不宣称精确超时延迟已测量。

每次正常选择器只选唯一自建fixture；后台保留段清空是应用级行为，不宣称完全浏览器RSS硬隔离。
本轮所有测试窗口均通过原生界面关闭，所有服务Ctrl-C退出130，桌面锁重新获取/释放通过。
旧成功fixture视频SHA256仍为791d2e8a4e9d6db5768448f3dd256106e9465f1a3071e9bcdbe75e048b358556。
没有录制Gamma，没有新模型调用、扩权、上传媒体、提交或推送。

剩余：显式取消/源结束/错误源选择、手工窗口缩放与浏览器工具栏变化、鼠标映射、其他浏览器、
正式CLI状态/manifest集成、Gamma精确身份与人工审看。异步身份通知/设置检查仍不是逐帧原子证明；
不能把fixture安全退出的结果推广为任意第三方私人页面已安全可录。


## CLI集成与Gamma元数据预检（本轮阶段结果）

实现位于`recording_agent/browser_probe.py`，浏览器资源仍在examples目录；按仓库方式运行，
尚未做安装包发布。原server.py保留为兼容启动器。新命令与正常record后端隔离：

```sh
python3.12 -m recording_agent browser-probe --kind fixture --root <fresh-private-root>
python3.12 -m recording_agent browser-probe --kind gamma-identity --root <fresh-private-root>
python3.12 -m recording_agent browser-probe-status --root <existing-private-root>
```

服务默认120秒（允许30..180），仅127.0.0.1，持现有桌面锁；输出地址供已授权操作者通过正常UI打开，
不自动调用浏览器内部接口。root必须在Git之外且0700；新请求写不可覆盖的schema_version整数1收据。
同root同参数只返回已保存状态，不再次启动服务；不同参数拒绝。无终态收据为unknown，不能以PID存在
认定正在录制或成功。客户端只有terminal=true且stream_stopped=true才闭环服务；未完成的选择器状态
保持未知，晚授权仍由页面检查绝对期限后立即停止。没有新异步record daemon。

fixture保存后独立检查WebM：单视频零音轨、逐帧尺寸/PTS、6±0.75秒PTS跨度、完整解码和所有四角色块。
成功为media_validated/fixture_media_validated，仅表示自建fixture媒体，不标记真实工具成功。
新校验函数已对历史真实fixture运行：176帧、6.471秒PTS跨度、704角检查、全解码通过且SHA不变。
新的预览消费/逐帧尺寸观察是Codex实施细节，尚未完成该版本的新fixture正向录制，不能冒用旧片验收。

Gamma模式只提供identity.html/identity.js，不提供fixture录制资源或video接收路由。
正常选择器选中既有《星屿轻记》唯一标签后，本机真实结果是：display_surface=browser、audio_tracks=0，
getCaptureHandle返回null（handle_present=false、observed_origin=null），reason=capture_handle_not_exposed。
此结果只说明Gamma没有向本次localhost捕获origin暴露handle，不证明Gamma对任何origin都不支持该能力。
应用没有创建MediaRecorder、video/canvas或帧读取器，frames_accessed=0；元数据读取后立即stop全部track，
stream_stopped=true。没有注入或修改Gamma页面，没有读取旧私人文档。

该CLI实际退出0，表示元数据诊断完成，capture_success=false、exact_document_bound=false，
并非Gamma录屏成功。同命令重放实际replayed=true；status返回一致终态，没有新开录或新服务。
服务已正常退出，桌面锁释放；预检窗口关闭后原生Window菜单不再列出本任务窗口，原Gamma窗口保留。

Gamma自动录制保持拒绝。不能用标题、相同origin或注入伪handle代替精确文档身份。
最小安全替代是继续使用自建页面验证录制器；若只需要既有演示内容，可在获准导出后录本地演示窗口，
明确标注为内容展示，不能当作Gamma操作实测录像。Gamma实际操作自动录制需要其官方提供可核验身份
的协作接口或另行review/授权的方案，本轮不扩权或装扩展。

新增9项离线边界测试全部通过，完整回归109项在20.111秒通过。覆盖私有路径/不覆盖、Host/Origin/
传输长度、Gamma路由拒收视频、终态双标记、schema版本、root重放等；JS语法也通过。
缩放、取消、鼠标及新预览链的本机实测尚未完成，仍不可称通用浏览器录屏功能已交付。
