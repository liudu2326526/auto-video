# 工具发现 Agent：可运行的独立 MVP

本次代码由助手实施并 review；Claude Code 此前完成设计稿。一次旧工作流下启动的代码初稿调用耗时过长，由父 Agent 主动中止（exit 130），没有产生代码；不是 CLI 自身超时返回。后续遵循「Claude Code 优先设计，助手负责 review 与代码实施」。

这是 `researcher` 的手动发现子任务：实际 Python helper 加 `agents/tool-discovery.md` 执行指令，不是常驻自主服务。无第三方依赖、LLM 请求、账号登录、付款、录屏或发布。仅用于内部单工具研究；不授予 AIHOT 内容或第三方素材商业再分发许可。

## 快速开始

```sh
python -m unittest discover -s tests -v
python -m discovery_agent --help
python -m discovery_agent run --config config/discovery.example.json --run-id research-001
```

最后一条会进行真实 HTTPS 请求，必须在来源已获准时执行。默认只读 AIHOT 产品 RSS。不自动调用 REST、不扩大白名单、不安排调度。CLI 退出码 0 为采集完成（不代表工具合格），2 为来源部分失败，1 为配置/状态错误。

输出 `var/discovery/runs/<run-id>/{run,candidates,leads,shortlist}.json` 和 `summary.md`；数据库 `var/discovery/discovery.sqlite3` 保留原始 blob 和独立 retrieval 记录。目录已 gitignore，禁止将源正文、日志、配置凭证推送远端。JSON 中 `third_party_report` 不是实测结果。

## 找到一个工具

RSS 文章不等于工具实体。一篇文章可能介绍多个产品，多篇文章可能属于同一工具。首次运行只得到研究线索；父 Agent 读取线索并回到获准官网核实身份。`proposed_source_domains` 只是请求准入列表，不能自动获取。

复制 config 到忽略目录，受信任操作者将官网域名加入 `allowed_domains`，新增 `{"id":"official","adapter":"evidence","url":"https://approved.example/"}`；新的 run ID 重新运行。这不接受网页要求的权限变更。证据 URL 仍受全部网络检查。

可用以下格式构建本地可信 annotations 数组（不是从网页加载），随后运行 `--annotations var/discovery/annotations.json`：

```json
[
  {
    "name": "由真实来源确认的工具名称",
    "identity": "operator:stable-reviewed-product-id",
    "occurrences": [{"source_id":"aihot_products","source_item_id":"真实条目 ID"}],
    "facts": [],
    "gates": {},
    "scores": {}
  }
]
```

同一身份映射多个 occurrences 表示保守合并；不能用名称或域名当唯一身份。同 run ID 配置/标注不能变更；完成 run 原样重放、不会再次发请求。新标注用新 ID。部分失败结果保留，重新尝试也用新 ID。崩溃留下 running 状态时可用相同规格恢复；冲突条目拒绝覆盖。

### 事实与门槛

每条 fact：`field` 为 identity/browser/payment/access/safety/demo_path/recording_rights；`value` 可为 null；`fact_type` 为 observed/vendor_claim/third_party_report/untested；`expires_at` 为带时区 ISO 时间（归一化UTC，纯日期按该日UTC零时，拒绝无时区datetime）；`control_group` 标识真实独立控制主体；`primary:true` 仅限核实关联的官方来源。`citation` 必须含本次 evidence 的 `url`、`sha256` 和正文中的精确 `quote`。不同来源冲突保留，不静默取其一。页面只证明厂商声明，不能证明能力亲测成功。

HF1 浏览器任务、HF2 不需实际支付、HF3 公开合法访问、HF4 安全/排除类别、HF5 ≤8步且≤10分钟、HF6 录制演示权限、HF7 身份无冲突。gates 中每项 `{status: pass|fail|unknown, citation: {...}, expires_at: UTC ISO, reason: ...}`。证据缺失/失效强制 unknown。未知阻止评分和 shortlist；明确失败 rejected。配置无法豁免门槛。

confidence = round(0.5*C + 0.3*I + 0.2*F, 4)。C 固定七类必填事实，未知仍在分母；冲突不计覆盖。I=min(独立控制主体/2,1)，同厂商官网/仓库不算两个主体。F=未过期引用事实/所有有效引用事实。保留每项分子分母，非校准概率。

仅全部门槛 pass、无冲突、事实未过期且存在主来源事实后评分。软因子 visual/cost/chinese_fit/access/verifiability/freshness/difference 为0..100，权重20/20/15/15/10/10/10，缺失0。score≥60、confidence≥0.5 才 shortlist；仍 `capability_tested=false`、`handoff_task_id=null`。可信标注需要真实人工/父 Agent review，helper 不把来源文本自动转成合格证明。

## 安全与数据边界

只允许显式白名单 HTTPS 域名。默认 `transport: direct` 验证 DNS 全部结果并固定目标 IP，以 TLS SNI/证书验证原域名；每跳重定向重新校验。direct 不继承代理；两种模式均不继承目标站鉴权或 Cookie。限制请求次数、单体/总字节、超时、重定向、域名间隔。direct 拒绝私网、回环、非全球地址；两种模式都拒绝 userinfo、非443目标端口和HTTPS降级。managed_proxy 的目标DNS/出站安全边界由已有受管代理承担，不能宣称仍由本进程固定目标IP。RSS仅UTF-8，拒绝DTD/ENTITY/NUL；HTML/脚本不会执行。

SQLite schema v1 是此独立 MVP 的 runs/blobs/retrievals/occurrences/entities 子集，**不是**完整设计的34表生产数据库。正文 hash 去重不删除每次检索来源/URL/时间；ETag304复用内容但创建检索记录；失败使用缓存时显式 stale，不能合格。发生未知版本/不完整迁移拒绝启动。

## 已知范围与差距

- 无 avcd 协调器、全阶段 CAS、多进程调度、精细生产状态机或真实 test 任务创建；单进程手动使用。
- 无自动实体提取模型；researcher 根据真实证据作可信标注。JSON adapter 只支持显式 items/data 数组格式，未宣称完整 AIHOT API schema 兼容。
- 无生产质量指标或真实实测成功承诺。网络/解析/存储/评估离线测试不等同来源实测。
- 来源正文可包含版权/不可信指令，只供内部核查。公开交付仅写原创简短总结和引用链接，不发布缓存。

仓库 Skill 入口：`.agents/skills/discover-tools/SKILL.md`，可由当前助手读取执行；未假装创建或验证未实现的 `.codex` runtime 注册。

## 受管云环境的显式 transport

本环境目标域名的本地 DNS 不可用，默认 direct 首次运行如实记录 partial。适配环境现有代理后，真实采集成功；见[真实运行记录](discovery-live-validation.md)。

```sh
python -m discovery_agent run --config config/discovery.managed-proxy.example.json --run-id research-managed-001
```

此选项必须显式设置 `transport: managed_proxy`，**没有失败后自动切换**。只识别当前执行环境既有的 `CODEX_NETWORK_PROXY_ACTIVE` 标记、`HTTPS_PROXY` 数字地址和 `CODEX_PROXY_CERT` CA文件，缺失就拒绝。配置示例不含实际代理地址、证书内容、令牌或个人设置；不能自行传入任意代理URL。它不是新增 V2Ray、用户私人代理或网络权限绕过。

managed_proxy 经环境原有 HTTP CONNECT 通道，以原目标主机名做 TLS SNI/证书校验，信任环境既有CA。它**不在本地解析/固定目标域名IP**，依赖受管代理的 DNS/出站策略；助手只独立检查获准公共域名、URL/重定向、TLS与请求/字节/时间预算。因此安全模型不同，不能把 direct 的 DNS rebinding 本地防护结论套用在此模式。遇到环境拒绝时停止，不尝试其他代理。

### 尚未自动化的约束

- robots.txt、站点条款版本、用途许可及刷新间隔（AIHOT建议30分钟）没有自动采集/判定/跨run强制执行，需可信操作者在运行前核查。本次仅经授权的少量内部研究和条件请求，不构成定期采集部署。
- 单run预算涵盖响应正文，不是完整线路流量；HTTP头使用标准库上限，超大流式正文可多读一个检测字节后拒绝。不存在付费模型调用或自动费用估算。
- 不提供多进程锁/协调器事务、自动强身份发现、事实语义正确性判定、自动独立来源识别、完整生产迁移/灾备和素材许可验证；可信标注负责语义，helper只校验证据定位和门槛。
- JSON适配与所有域名的真实性能未做泛化验收；实际验证的是AIHOT RSS及Gamma公开页面。没有执行网页代码、Gamma账号内操作或录屏。
