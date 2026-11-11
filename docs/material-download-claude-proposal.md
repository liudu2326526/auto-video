> Claude Code 2.1.310 实际返回的只读提案；未访问仓库。以下是设计讨论，不是已实现功能或执行指令。实施与边界以 material-download.md 为准；Codex 负责审查和代码。

# 素材自动下载 + 本地库：只读方案（MVP）

> 说明：按要求未调用任何工具、未读取仓库、未写文件。以下按你给出的接口描述推导；文末列出实现前必须先核对的 5 个事实。没有做任何法律判断，也不声称能替代人工审核。

---

## 1. 核心决策：重定向怎么做（这是整个方案的关键）

你的约束与现有 `Fetcher` 存在直接冲突：`max_redirects=0` 时，`Fetcher` 遇到 3xx 会在内部预算检查处直接报错，**拿不到 `Location`**，所以"逐跳跟随 + 每跳先查 robots"无法在完全不改共享模块的前提下实现。

三个选项：

| 选项 | 做法 | 代价 | 结论 |
|---|---|---|---|
| A | MVP 严格禁跳：任何 3xx = 失败（`redirect_not_allowed_mvp`） | 记录里的 `download_url` 必须是**最终直链**，由人工在建档时确认；`Special:FilePath` 这类跳转入口一律不可用 | 最安全、零改共享模块，作为兜底默认 |
| B | 给 `Fetcher` 加**默认关闭**的 `follow_redirects: bool = True`；为 False 时 3xx 不抛错，返回 status/headers/final_url(=请求 URL)，**body 丢弃不嗅探**，`Location` 仍做 scheme/域校验但**不发请求** | 约 15–25 行改动，纯增量，默认值保持旧行为，旧测试不受影响 | **推荐** |
| C | 在新模块里重写 DNS pin/TLS/预算 | 重复实现安全原语，最容易出漏洞 | 否决 |

**B 的不变量**（写进注释和测试）：发往"下一跳"的请求只能由我们的 hop 循环发出；`Fetcher` 自己永远不跟跳。robots 检查因此天然发生在"取到 `Location` 之后、发出下一跳之前"。

### 逐跳循环（伪流程，判据而非代码）

```
origin_ok = origin ∈ provider.origins (精确 host 匹配，无通配)
            ∧ scheme == https ∧ port ∈ {空,443} ∧ host 非 IP 字面量 ∧ 非 IDN 裸 Unicode
            ∧ URL 无 userinfo ∧ 无 %0d%0a
robots[origin] = ensure_robots(origin)   # 未放行 → 直接 block，不发媒体请求

url = record.source.download_url; hops = 0; seen = {url}
loop:
  assert url 通过上面全部 URL 判据；assert robots[origin(url)] 已放行
  消耗 1 个总请求预算
  resp = fetcher.get(url, max_redirects=0, follow_redirects=False)   # 选项 B
  if resp.status in (301,302,303,307,308):
      if hops >= policy.max_hops: block(redirect_hop_limit)
      loc = parse_location(resp.headers)            # 相对/协议相对/反斜杠/超长 全部规范化
      若 loc 解析后非 https / 含 userinfo / 端口非 443 / 有 %0d%0a → block(redirect_downgrade|url_has_userinfo)
      若 host 不在 provider.origins → block(redirect_domain_not_allowed)   # 不碰未知域，连 robots 都不查
      若 loc 已在 seen → block(redirect_loop)
      robots[host] = ensure_robots(host)            # 新域先查，再跳
      未放行 → block(robots_*)；放行 → seen.add(loc); url = loc; hops += 1; continue
  if resp.status != 200: → 分类 HTTP 错误（403/429/5xx/其他）
  else: 进入校验管线
```

要点：

- **hops 只算媒体跳转**，robots 抓取单独计费但共用总预算；`request_budget ≥ 1(robots) + max_hops + 1`，启动时校验，配置不够直接 `policy_invalid`。
- 域白名单**精确 host 列表**。不要用 `endswith(".wikimedia.org")`——那会放行 `evil.wikimedia.org`。
- 每一跳都是新 origin → 新的 DNS 全结果校验与连接 pin，绝不复用上一跳的连接或解析结果。
- 若你坚持完全不改 `net.py`：把选项 A 设为唯一行为，并在 CLI 上提供 `--explain-redirect`（只报告"该 URL 会跳转，请改为最终直链"，不跟随），这个诊断本身也可以用 `HEAD`… 但 HEAD 不受支持，所以实际就是"3xx 即失败 + 打印 Location 供人工改档"。

---

## 2. robots.txt 策略（先查后跳，失败闭合）

按 origin 缓存（MVP 仅进程内缓存，不落盘，避免旧缓存掩盖后来收紧的规则）。用同一个 `Fetcher`（direct、TLS、公网 IP 校验），固定 UA token（与 `auto-video-discovery/0.1` 区分，建议 `auto-video-material/0.1`），`Accept-Encoding: identity`，自身 `max_redirects=0`。

| robots 结果 | 决策 | 理由 / 备注 |
|---|---|---|
| 200 + 允许 | 放行，记录 `matched_rule`、UA、抓取时间 | 仍**不是**授权证据 |
| 200 + Disallow 命中 | 阻断 `robots_disallow` | |
| 200 但含 `*` / `$` 且 `urllib.robotparser` 无法可靠匹配 | 阻断 `robots_ambiguous`（fail-closed） | 记录原始行；不要"猜"通配语义 |
| 404 / 410 | **放行**（RFC 9309 的 unavailable→allow），事件记为 `robots_absent` | 只在 provider 已审阅的前提下放行 |
| 401 / 403 | **阻断** `robots_forbidden_403`（比 RFC 更保守，可配置） | 403 常意味着反爬，MVP 不试探 |
| 429 | 阻断 `robots_rate_limited_429`，本轮对该 origin 冷却，不重试 | 不改 UA、不加 cookie |
| 5xx | 阻断 `robots_unavailable_5xx` | 与 RFC 的 unavailable→disallow 一致 |
| 超时 / 连接错误 | 阻断 `robots_timeout` / `robots_network_error` | |
| 3xx | 阻断 `robots_redirect_blocked` | MVP 不为 robots 做逐跳；要跳就先人工改 provider 配置 |
| 体积超 `robots_max_bytes`(建议 512KB) | 阻断 `robots_oversize` | |

明确写进文档与代码注释：**robots 只回答"能不能爬"，不回答"能不能用"**。许可证据只能来自 record 里人类录入的 `rights.evidence`，且必须包含 `scope=terms`（若 policy 要求）与 `scope=license`。

---

## 3. 许可闸门（纯机械匹配，不做法律判断）

在任何网络 I/O 之前执行，全部条件必须同时成立：

1. `record` 由 `--record <id>` 从 Library 只读查出，且 `source.access == "public_no_login"`（否则 `access_not_public_no_login` / `login_required_unsupported`，MVP 直接硬失败，不做登录、不做 Google 接管）。
2. `rights.status == "known"`，`license_id ∈ policy.allowed_license_ids`（显式枚举，如 `CC0-1.0`、`PDM-1.0`）。
3. `commercial_use` / `derivatives` / `allowed_distributions` 逐字段满足 policy 的**声明式**要求；`share_alike_required`、`attribution_required` 只做记录与后续提醒，不在这里做推理。
4. `evidence` 非空，且至少一条 `scope` 覆盖本次 `download_url` 或 `page_url` 的 host；`checked_at` 距今 ≤ `evidence_max_age_days`（建议 180）；`excerpt` 原样保留，**不解析其内容**。
5. 满足后生成一个**只能由该函数构造**的能力对象（`_minted` 私有令牌 / 冻结 dataclass），携带 `policy_sha256`、`record_id`、`allowed_origins`、`max_bytes` 等。下载器只接受这个对象，无法被单独调用而绕过闸门——这就是"下载凭据结构"，它**不含任何密钥或密码**。
6. `policy` 必须是本地常规文件；`--policy` 传 URL 一律 `policy_from_network` 拒绝。政策内容绝不来自被下载的页面。

不许做的事：不因为 `robots` 允许就放行未知许可；不因为"看起来是公有领域"就推断；不自动填写 `review` 结论——新记录一律 `pending`，`handoff` 仍由既有的人工审核不变量拦住。

---

## 4. 下载管线（隔离 → 嗅探 → 校验 → 原子入库）

默认 `--dry`（零网络、零写库、零临时文件），`--execute` 才真正执行；MVP 只接受单个 `--record`，拒绝 `--all`。

1. **预检**：闸门 + URL 判据 + provider 存在且 `enabled` + robots 放行（dry 模式不抓 robots，只在报告里列出"将在 execute 时抓取哪些 origin"）。
2. **隔离**：`tempfile.mkdtemp(dir=<library>/.tmp, prefix=run_id)`，目录 0700；文件名用固定占位（`payload.bin`），**不用**服务器给的 `Content-Disposition` 文件名做路径（仅清洗后作为建议名记入 receipt）。临时目录与最终库同文件系统，保证后续 rename 原子性。
3. **抓取**：见第 1 节 hop 循环。同时施加三层大小限制：`Content-Length` 预判 → `Fetcher` 流式预算 → 落地后 `len(body)`。字节只在内存/临时文件，不落库路径。
4. **传输层拒绝**：任何 `Content-Encoding` 存在即拒（`content_encoding_forbidden`），并额外拒首字节 `1f 8b`(gzip)、`28 b5 2f fd`(zstd)、`PK\x03\x04`(zip)；`Transfer-Encoding` 异常或 `Content-Range`/206 均拒。
5. **类型嗅探**：只看魔数，白名单 = `{jpeg,png,gif,webp,mp4,webm,mp3,ogg,wav}`。SVG/HTML/zip/可执行恒拒。
   - `sniffed` 不在白名单 → `mime_not_allowlisted`。
   - `declared` 与 `sniffed` 都有效但不一致 → `mime_mismatch`。
   - `declared == application/octet-stream` 或缺失 → 以 `sniffed` 为准，receipt 标 `mime_source=sniff_only`。
   - `declared` 本身在白名单但 `sniffed` 是文本/压缩 → 拒（防伪 MIME）。
   - `sniffed` 以 `<` 开头（`<!DOCTYPE`/`<html`/`<svg`/`<?xml`）→ 一律拒。
   - 廉价结构校验：PNG 尾部 `IEND`、JPEG 尾部 `FFD9`、RIFF 的 `size == len-8`；不符 → `truncated`。
6. **长度/哈希**：`Content-Length` 存在则必须等于 `len(body)`，否则 `length_mismatch`（含"传输中断但 200"的情形）。之后 `sha256(body)`。
7. **复用判断**（在写库前）：
   - 命中相同 SHA256 → 不写新 blob，走 `local_reuse_by_hash`；
   - 未命中但同 URL 已有**通过校验的**记录 → 仍可复用其 blob，记 `local_reuse_by_url`；
   - **两种情况都必须**：新记录用自己的 `rights` 重建，`review` 状态为 `pending`，receipt 里 `approval_inherited=false` + `reused_from_record_id`。绝不复制旧记录的审核结论。
8. **落盘**：校验通过后 `os.fsync` → `os.replace(src, blobs/<sha[:2]>/<sha>)`；目标已存在则**只比较不覆盖不删除**。失败路径 `finally` 清理临时目录，保证最终路径永远不出现半文件。
9. **入库**：调用既有 `Library.add(record, file, download)`（保留其 SHA256 去重语义）。建议给它加一个**默认 None** 的 `events: list | None = None` 关键字，让 record + review + receipt + 事件在同一事务提交；不传时行为与现在完全一致。
10. **事后校验**：`add` 返回后按 library 给出的 blob 路径**读回重算 SHA256**；不一致 → 记 `post_store_verify_failed` 事件、退出码 5、不修改任何状态（记录本就 pending，`handoff` 天然被拦）。宁可"入库了但标记失败"，也绝不"假成功"。
11. **失败事件**：任何阶段失败都用独立短事务写 `download_events`（阶段、错误码、HTTP 状态、跳转链脱敏、robots 决策、已收字节数）。即便 record 插入失败也必须留下这条事件；反之，失败**绝不**产生 record。

退出码固定：`0` 成功/已存在、`2` 策略阻断、`3` 网络、`4` 校验不符、`5` 许可闸门或入库后校验失败。

---

## 5. 先落地的"不影响旧测试"的增量结构

**Phase 1（必须先做，且不改变任何现有行为）**

- `material_agent/audit.py`
  - `NetworkAuditEvent`：`event_id, run_id, occurred_at(UTC, ISO-8601 Z), stage{policy_load|license_gate|url_validate|robots|connect|response|redirect|sniff|length|hash|store|commit|cleanup}, origin, url_redacted, method, http_status, outcome{ok|blocked|error}, reason_code, detail(dict)`。
  - `NullAuditSink`（默认）+ `SqliteAuditSink`。**永不记录** cookie / Authorization / 完整 query（query 只留 `?<sha256前8位>`）。
  - 现有 `Fetcher` 不接线：只提供 `null` sink，等 Phase 3 由下载器注入。
- `material_agent/download_policy.py`
  - 载入本地 JSON（stdlib，不引入 PyYAML），严格 schema 校验（未知键报错而非忽略），计算 `policy_sha256`。
  - 字段：`policy_id, version, allowed_license_ids[], rights_requirements{}, allowed_origins[]（精确 host）, allowed_media_types[], max_bytes, timeout_s, max_hops, request_budget, robots{mode,ua_token,on_4xx,on_5xx,max_bytes,timeout_s}, evidence_max_age_days, require_terms_evidence`。
- `material_agent/providers.py`
  - 已审阅的固定 provider 注册表：`{provider_id, origins[], default_license_allowlist[], enabled, requires_login:false, reviewed_by, reviewed_at, notes}`。MVP 只允许 `enabled && !requires_login` 的条目；`reviewed_by/at` 为空视为未审阅而拒绝。
- `material_agent/capability.py`
  - `DownloadAuthorization`（冻结 dataclass + 私有构造令牌），字段同上，仅由闸门函数产出。
- `migrations/0002_*.sql`
  - `audit_events`、`download_events` 两张新表 + `schema_version` 表；**只加表不改表**。若既有的 `download.method` 上有 CHECK 约束并需要新增值，则另起 `0003` 重建该表（旧行原样搬运），并把这一步单独放在可回滚提交里。

Phase 1 完成的验收：`pytest` 全绿，且这些模块没有被任何现有代码 import（可用一条测试断言 import 图）。

---

## 6. 陷阱清单（按踩坑概率排序）

1. **`max_redirects=0` 是陷阱**：它不是"拿到 3xx 但不跟"，而是"报错"。别在调用侧 try/except 猜 Location——错误里不一定有 headers。
2. **`Location` 规范化**：相对路径、`//host/path`、大写 scheme、反斜杠、超长值、`user:pass@`、`file://`、`%0d%0a`。必须先在本地规范化+校验，再拼成下一跳。
3. **域名后缀匹配漏洞**：`evilwikimedia.org`、`wikimedia.org.evil.com`、`evil.wikimedia.org`。MVP 用精确 host 列表。
4. **DNS 重绑定**：不要自己做 `getaddrinfo` 再看结果；一切走 `Fetcher` 的 pin 流程。robots 抓取同样走它，否则 robots 成了绕过 pin 的旁路。
5. **robots 的体积/超时/跳转**：robots 自身也是不可信输入，必须限长限时、禁跳、禁压缩。
6. **`Content-Length` 撒谎**：偏小（截断）、偏大（挂住）、缺失（chunked）。三层校验缺一不可；只要 200 且长度不符就判截断。
7. **压缩旁路嗅探**：`Content-Encoding: gzip` 让魔数变成 `1f8b`，一旦解压就绕过白名单判断。请求侧禁协商 + 响应侧见即拒 + 魔数兜底。
8. **伪 MIME 与 polyglot**：`Content-Type: image/png` 实为 zip/HTML；PNG+尾部 HTML。嗅探优先、尾部校验、`<` 开头即拒。
9. **`Content-Disposition` 文件名**：`../`、`/`、控制字符、超长、UTF-8 与 latin-1 混淆。只用 basename，字符集收紧到 `[A-Za-z0-9._-]`，仅作元数据。
10. **符号链接/TOCTOU**：库目录内 `blobs/` 用 `O_EXCL|O_NOFOLLOW` 打开；临时目录 `mkdtemp` 0700；不覆盖已存在 blob。
11. **长事务**：绝不在持锁期间做网络 I/O。`add` 的事务要短，事件写入单独短事务。WAL + `busy_timeout`。
12. **复用继承审核**：最容易写错的一处——`find_by_hash` 命中时顺手 copy 了旧 `rights`/`review`。必须显式重建 + `approval_inherited=false` + 一条回归测试断言 `handoff` 被拒。
13. **时间**：只用注入的 `now_fn` 产生 UTC；不要混用本地时间；`retrieved_at` 与 `checked_at` 的语义不要混（前者是本次抓取，后者是人类核查许可的时间）。
14. **网页里的指令**：robots/HTML/`Link` 头/meta refresh 全部只当数据。不执行、不解释、不据此改策略。
15. **dry 模式产生副作用**：dry 写一行日志都不该有。测试要断言 DB 文件内容哈希与临时目录均未变。
16. **并发**：两个进程同时下同一 URL → 一方写 blob 一方命中；用 `BEGIN IMMEDIATE` + blob 存在性检查（不删除、不覆盖）保证收敛。
17. **未知 provider / 未审阅 provider**：`enabled` 与 `reviewed_at` 双条件，避免"配置里加一行就放行"。

---

## 7. 测试矩阵

约定：除最后一行外全部**离线**，用假的 `Fetcher`/可注入 resolver（现有代码已有 DNS 全结果校验入口，测试可 monkeypatch）；网络层可另做一个本地 TLS 服务 + 注入 CA（若 `Fetcher` 不暴露 `ssl_context`，则不进这一层，用协议桩覆盖 hop 逻辑）。

| # | 场景 | 注入 | 期望 reason_code | 断言无副作用 |
|---|---|---|---|---|
| 1 | 许可 unknown | record.rights.status=unknown | `license_unknown` | 零网络、零临时文件 |
| 2 | 许可不在白名单（CC-BY） | policy 只允许 CC0/PDM | `license_not_allowlisted` | 零网络 |
| 3 | 证据过期 / 缺 scope=license | checked_at 400 天前 / 空 evidence | `license_evidence_stale` / `license_evidence_missing` | 零网络 |
| 4 | `access=login_required` | 记录声明需登录 | `login_required_unsupported` | 无任何认证尝试 |
| 5 | `--policy https://...` | URL 作为策略 | `policy_from_network` | 零网络 |
| 6 | http / 非 443 / IP 字面量 / userinfo / Unicode host | 各一条 | `url_not_https` 等 | 零连接 |
| 7 | DNS 返回私网地址（127.0.0.1、10/8、169.254.169.254、::1、fc00::/7） | resolver 桩 | `dns_private_ip` | 无 TCP 连接 |
| 8 | DNS 重绑定：第一次公网、后续私网 | 序列桩 | `dns_rebind` | 无二次连接 |
| 9 | robots 404 | 桩 | 放行，事件 `robots_absent` | — |
| 10 | robots 200 + `Disallow: /media/` 命中 | 桩 | `robots_disallow` | 媒体请求计数 = 0 |
| 11 | robots 200 含 `*`/`$` 不可靠匹配 | 桩 | `robots_ambiguous` | 媒体请求 = 0 |
| 12 | robots 403 / 429 / 500 / 超时 / 3xx / 1MB | 桩 | 对应 `robots_*` | 媒体请求 = 0 |
| 13 | 单一跳转到**未知域** | 302→`cdn.evil.com` | `redirect_domain_not_allowed` | 对该域**零请求**（连 robots 都没有） |
| 14 | 跳转到白名单新域，robots 允许 | 302→`upload.example.org` | 成功 | robots 请求**先于**媒体请求（事件顺序断言） |
| 15 | 跳转到白名单新域，robots Disallow | 同上 | `robots_disallow` | 第二跳媒体请求 = 0 |
| 16 | 跳转链 4 跳（max_hops=3） | 302 链 | `redirect_hop_limit` | 请求数 = 4 |
| 17 | A→B→A | 环 | `redirect_loop` | 请求数 = 2 |
| 18 | https→http 降级 | 302 到 http | `redirect_downgrade` | 无明文连接 |
| 19 | MVP 选项 A：任何 3xx | 301 | `redirect_not_allowed_mvp` | 无第二跳 |
| 20 | 媒体 403 / 429 / 503 | 桩 | `http_403` / `http_429` / `http_5xx` | 不重试、不改 UA |
| 21 | 200 但 `Content-Type: text/html` + HTML 体 | 桩 | `mime_not_allowlisted` | 无 blob |
| 22 | 200 但 `image/svg+xml`、`application/zip`、`text/plain` | 桩 | 同上 | 无 blob |
| 23 | 声明 `image/png`，实际 zip 魔数 | 桩 | `mime_mismatch` | 无 blob |
| 24 | 声明 `application/octet-stream`，实际合法 PNG | 桩 | 成功，`mime_source=sniff_only` | — |
| 25 | `Content-Encoding: gzip` 且体为 gzip | 桩 | `content_encoding_forbidden` | 无 blob |
| 26 | 无 CE 头但首字节 `1f8b` | 桩 | `content_encoding_forbidden` | — |
| 27 | `Content-Length: 100`，实收 80 | 桩 | `length_mismatch` | 临时目录已清理 |
| 28 | `Content-Length: 10`，实收 100 | 桩 | `length_mismatch` | — |
| 29 | 头声明 5GB | 桩 | `too_large_header` | 未读体 |
| 30 | 无长度、体超 `max_bytes` | 桩 | `too_large_stream` | 临时文件 ≤ 上限 |
| 31 | 读到一半超时 | 桩 | `timeout` | 半文件已删、事件已写 |
| 32 | PNG 截断（缺 IEND） | 桩 | `truncated` | 无 blob |
| 33 | 合法 CC0 PNG | 桩 | 成功 | record 存在、`review=pending`、receipt v2 字段齐全 |
| 34 | 同 hash 第二次（不同 record，许可已知） | 桩 | 成功，`local_reuse_by_hash` | **`approval_inherited=false`**，`handoff` 被拒 |
| 35 | 同 hash 第二次（新 record 许可 unknown） | 桩 | `license_unknown` | 复用不发生（许可闸门在前） |
| 36 | 同 URL 已有 blob | 桩 | 成功，`local_reuse_by_url` | 仍新建 pending 记录 |
| 37 | `--dry`（默认） | 无 | 输出计划 | DB 哈希不变、无临时目录、零网络 |
| 38 | DB 提交失败 | 注入 sqlite 错误 | `db_error` | 只留孤儿 blob，无 record，事件已写 |
| 39 | `add` 返回后读回哈希不符 | 注入篡改 | `post_store_verify_failed`，退出 5 | 记录为 pending，不可 handoff |
| 40 | 旧测试回归 | — | 全绿 | `Fetcher` 默认行为逐字节不变 |
| 41 | 线上烟测（`MATERIAL_LIVE_TEST=1`，手动） | 真实 CC0 图 | 成功 | 人工核对页面许可后再执行 |

线上烟测建议：选一张 `upload.wikimedia.org` 上的 CC0 图片直链（人工先在文件页确认 `CC0 1.0` 与许可 URL），`rights.evidence` 里存好 `scope=license` 与（若 provider 要求）`scope=terms` 的原始摘录。**不要**用 `Special:FilePath` 这类跳转入口（除非启用选项 B）。烟测默认跳过，不进 CI。

---

## 8. 实施阶段（每阶段独立可验收）

1. **Phase 0 冻结现状**：为 `Fetcher` 与 `Library` 补特征化测试（成功路径、3xx 报错、预算、`add` 去重、`handoff` 拦截），作为后续回归基线。
2. **Phase 1 增量骨架**：`audit.py` / `download_policy.py` / `providers.py` / `capability.py` + `0002` 迁移。不接线，全绿。
3. **Phase 2 网络层**：`Fetcher` 的 `follow_redirects=False`（默认 True）+ `ensure_robots`（缓存、限长、限时、禁跳、状态矩阵）。
4. **Phase 3 管线**：URL 判据 → hop 循环 → 嗅探/长度/哈希 → 临时隔离 → `Library.add` → 读回校验 → 事件。
5. **Phase 4 CLI**：`material download --record --policy [--dry|--execute] [--provider]`，退出码固定，报告里打印跳转链、robots 决策、阻断原因。
6. **Phase 5 收尾**：孤儿 blob 的 `library gc --dry-run` 只读报表；文档写明"不自动法律判断、不自动人工审批、MVP 不支持登录"。

---

## 9. 明确不做

- 不做登录 / cookie / Google 接管 / `managed_proxy` / 任何 Authorization。
- 不做 robots 之外的站点条款自动解析，不把网页文本当指令或授权。
- 不自动改写 `review` 结论、不自动 `handoff`、不在复用路径上继承审核。
- 不支持批量、不支持压缩包、不支持 SVG/HTML/可执行、不支持 3xx 之外的协议特性（206、Ranges、ETag 续传）。
- 不对许可做任何法律推理或"合理性"判断。

---

## 10. 实现前必须先核对的 5 个事实（我未读代码）

1. `Fetcher.get` 的签名与"3xx 预算报错"的确切形式；是否已有可注入的 resolver / `ssl_context`（决定测试能否离线覆盖网络层）。
2. `download` receipt 是 JSON blob 还是独立列；`method` 是否有 CHECK 约束（决定是否需要 `0003` 表重建）。
3. `Library.add` 是否已在一个事务内完成 record + review + receipt；是否已有 `find_by_sha256` / `find_by_url` 之类的只读查询可复用。
4. `review` 状态机与 `handoff` 的判定字段名（`pending` / `approved` 的实际取值），以便复用而不再造状态。
5. 是否已有 `schema_version` / 迁移机制；若没有，`0002` 需要先补一个最小迁移框架。

待你确认的点（可直接回"按推荐"）：

- 重定向：**选项 B（给 Fetcher 加默认关闭的 `follow_redirects=False`）**，还是**选项 A（MVP 严格禁跳）**？
- robots `401/403` 是否接受我的**偏保守阻断**（比 RFC 更严）？
- provider 白名单是否就用 `providers.py` 静态注册表（不引入外部配置文件）？
