# 浏览器内容区捕获：安全原型与阻塞

2026-11-03，Codex本机实施。**当前不是可用的Chrome内容区录制功能。**
新Chrome正式录制会在建流前拒绝，原因是无法证明AX几何与视频帧的原子关联；
不会静默沿用旧整窗路径。历史request回放、状态、文件及哈希不改写。

## 已完成

- 用户希望排除标签栏、地址栏和浏览器调试提示。没有隐藏提示、改安全设置或用固定像素。
- 目标Chrome的只读AX探针仅读role/geometry，有界匹配已批准CG窗口；WebArea与ScrollArea
  交集可取得当前viewport。当前窗口比旧记录移动1点，必须重新测量，不能复用旧坐标。
- 本机Retina2安全标记窗口实际证明：独立窗口sourceRect使用窗口局部points，即全局viewport
  减filter.contentRect原点。1440×600输出保留网页四角，未出现顶部EXCLUDED标记。
  此项为静态真实捕获验证；不能说所有动态浏览器布局已经验证。
- `CaptureCrop.swift`实现全局/局部/像素转换、向内取整、偶数尺寸、边界与像素预算，
  自动测试覆盖1/2/3/4倍尺度、负坐标、奇数尺寸、无效/越界输入及鼠标输出坐标映射。
- `MemoryCropWriter.swift`使用AVAssetWriter的无文件URL分段输出，压缩数据仅保留在有界RAM；
  `BoundedSegments.swift`检查分段顺序/重复/缺口/长度和预算，任何拒绝会清空已保留数据。
  外部AX-only证据无法取得可发布的字节；授权的测试生成帧才可完成封装。
- 编码组件实测30个64×64生成帧形成1秒H264、零音轨MP4，全解码通过。
  进程峰值约27MB、应用保留段峰值2949字节。这不是全分辨率RSS上限保证。
- 新增7项自动测试，包含已有分段后发生几何失效并清空、预算超限、错误尺寸及外部证据不足
  均不生成任何输出文件；原有93项与新增项合计100项通过。
- 对获准Gamma发起3秒请求，真实得到`browser_content_atomicity_unproven_no_capture`，
  退出2、无start/recording事件、无媒体。它是拒绝路径实测，不是Gamma裁切录屏成功。

## 未解决的竞态与明确限制

SCK的sourceRect与AX几何不是原子快照。轮询或通知可能在工具栏变化后才返回，甚至漏掉
短暂变化；把整次数据留RAM，只解决延迟落盘，不能证明未观测瞬态不存在。
当前没有以这一假设放行Chrome正式视频。新组件尚未接成可用的浏览器录制后端，
也没有完成SCK动态标记fixture到内存编码再发布的完整端到端接线。

应用自己保留的压缩段受配置预算限制（内部上限256MiB、最多64段），裁切像素上限
4194304。编码器/系统分配不受此对象的硬隔离控制；不宣称完整进程内存沙箱。
编码器测试只用生成内容，没有浏览器像素、音频或新增系统权限。

后续应优先验证浏览器自身提供的tab/viewport捕获接口，结构上排除浏览器UI，
而不是继续用更快的AX轮询声称消除了竞态。只能使用已授权、受支持的接口；
新扩展权限、原始CDP或其他未批准通道不能自动引入。

## 方案来源与代码状态

现有Claude Code/muskapis受限无工具调用先210秒、后一次获准短试60秒均超时，
进程已回收、正文均0字节；无Claude草稿或review结果，计费终态未知。
本原型与保守边界由Codex撰写、检查及实施，没有冒充Claude产物。

现有未提交修改完整保留，不提交、不推送；媒体、快照、运行日志及编译物均在仓库外。
新Chrome拒绝保护已接入正常CLI；纯几何/内存编码组件由自动测试直接编译调用，
不可把测试组件误称为已接通的Chrome内容区产品能力。

## 浏览器原生视频接口：后续调研与未完成实验

官方Screen Capture规范区分browser/window/monitor surface。browser指浏览上下文的渲染内容，
无须依赖AX工具栏坐标裁切；`displaySurface: browser`只是选择器偏好，必须在授权后检查
实际track settings，不能把请求参数当作已选择的捕获源。
[W3C Screen Capture](https://www.w3.org/TR/screen-capture/)还规定显示捕获的缩放保留比例、
不得裁去源内容；`resizeMode: crop-and-scale`在这一接口表示缩小而非裁切。

[Chrome的选择控制](https://developer.chrome.com/docs/web-platform/screen-sharing-controls)
提供排除整屏、排除动态换源入口、禁音频等选项，但不提供直接指定任意目标标签页的网页权限。
每次必须经过浏览器选择/临时授权；不修改系统权限或隐藏提示。
[Capture Handle](https://developer.chrome.com/docs/web-platform/capture-handle)需要被捕获页面主动
暴露origin和handle；缺失时不能证明是指定的Gamma文档。其change事件也不能当作所有内容
跨源变化的逐帧安全证明。Chrome扩展tabCapture则需要扩展权限和用户调用，当前未安装/授权。

本轮在工作流补充要求到达前，Codex新建了
[`examples/browser-viewport-probe`](../examples/browser-viewport-probe/README.md)实验代码：
仅127.0.0.1服务、自建动态内容和Capture Handle、浏览器原生视频流、MediaRecorder有界保留、
本机独占写入。没有把截图拼成视频，没有注入Gamma代码，也没有接入正式Agent后端。

实际Chrome UA为156。第一次后台页按钮得到`InvalidStateError`，未显示选择器；激活自建页后
选择了自建fixture，确实取得`displaySurface=browser`、30fps settings、无音轨的视频流，
并触发MediaRecorder start。约51.8ms后尺寸变化触发discard，无任何WebM/MP4文件落盘。
因此30fps只是返回的设置值，不是经视频PTS/帧数验证的实际帧率；指针mode为motion，未验收。

之后补了编码前最多2秒的稳定尺寸检查（连续5次相同观测），尚未成功实测。一次CUA调用
30秒超时后核查按钮仍ready，才从现有前台按钮恢复请求，没有并发开录。第二次选择器已选中
fixture，但在点击“分享”前收到主任务澄清：新实质设计必须先有Claude草稿或用户确认替代。
已立即点击“取消”，浏览器返回`NotAllowedError: Permission denied by user`；这是代理取消，
不是系统权限缺失结论。其后未重试、未访问/录制Gamma，Chrome正式录制保护仍生效。

本地服务已Ctrl-C正常停止（进程退出130）。关闭两个自建标签页的CUA组合调用超时，随后
精确查询自建fixture返回`Browser is not available: chrome`，所以标签页关闭结果仍不确定；
最后两个捕获请求均已discard并停止track，无媒体，不能把浏览器连接断开当作仍在录制。
未保存选择器界面或无关标签页内容。

实际视频的完整解码、时长、帧率、四角/顶部排除、缩放/工具栏变化、鼠标与Safari/Firefox/Edge
兼容性均待验。此轮新增JS与Python语法检查通过；没有新增通过的端到端视频测试，
前述100项测试属于前一阶段。尚待决策：等待Claude可用，或用户允许Codex继续这份明确标注的
实验方案，再执行有限fixture验证；不可把当前状态称为浏览器内容区录制完成。

## 最新续测

用户已明确允许一次Claude重试及失败时由Codex继续。本次Claude在35.993秒退出0并有真实草稿，
随后Codex review及实现，完成了browser surface的实际fixture视频：176帧、1750×958、零音轨、
全解码通过、所有704处四角检查通过。PTS跨度6.471秒、观测平均27.04fps，非固定30fps。
正式CLI/Gamma仍未放行，负例与浏览器几何/鼠标验证待连接恢复。
[完整草稿、review和当前结果](browser-viewport-validation.md)。前述暂停/无成功视频描述是历史检查点。
