> 设计来源：Claude Code 2.1.310，只读方案输出；Codex 负责审核和实施。以下是提案，不是已实现功能，也不是法律结论。实际接口、功能和限制以 material-agent-mvp.md 为准。

# auto-video：素材搜集 Agent + 本地素材库（方案设计 v1）

> 只做设计。所有落盘路径都在新命名空间 `assetlib/` + `assetlib_data/` 内，**对编剧/剪辑共享文件只读**。程序输出是"证据台账 + 策略闸门"，不等于法律审查，也不等于语义/内容审核。

## 0. 一句话结论

MVP 是**"操作员导入 + 哈希绑定审核 + 用途闸门 + 可重放 handoff"**的纯标准库闭环；不做爬虫、不做批量下载、不做转码、不自动判定许可。**许可未知 = 不进可用队列**，这一条由 `gate` 单点函数保证，`handoff`/`verify`/`retrieve --usable` 全部复用它。

## 1. 架构与冻结边界

```
分镜 storyboard (只读)
   └─ query.plan() ─→ plans/q_*.json         # 只含白名单关键词 + 场景指针
操作员/AI 手工检索（站外）
   └─ candidate.json + imports/xxx.mp4       # 人工下载，agent 只收元数据
        └─ store.import_file() ─→ content(sha256) + source_assertion + license_claim
             └─ review.create() ─→ 人类署名审核，绑定 subject_digest
                  └─ gate.evaluate(asset, use_profile, at) ─→ 可用/不可用 + 理由码
                       └─ handoff.build() ─→ out/<handoff_id>/  (path, sha256, rights, rights_basis)
                            └─ handoff.verify() # 渲染前再验一次，含过期与文件篡改
编剧 helper (只读契约，反向输出 assets.json，最大只能到 available_unverified)
```

- 三层：**证据层（不可变）→ 判定层（可重算）→ 交付层（可重放）**。任何"状态"字段都不许当作事实来源，只作为派生缓存，读时用 digest 重算。
- 不新增共享模块，不 import 编剧/剪辑源码；只做**文件级契约适配**。`paths.py` 内置 `FORBIDDEN_WRITE_ROOTS`，所有写操作前校验 `out_dir` 不在共享目录内，并有测试守住。
- schema2 的逐句 evidence 并行开发中：**读取侧容错**（未知字段忽略、缺失字段降级），**写入侧只写自己的 schema**；分镜取快照 `storyboard_snapshot_sha256`，plan 里带指针不复制原文。

## 2. 模块（全部标准库：sqlite3/hashlib/json/argparse/pathlib/os/stat/uuid/datetime/unittest）

| 模块 | 职责 | 关键点 |
|---|---|---|
| `db.py` | 连接、迁移、PRAGMA、追加式触发器 | `user_version`/`application_id`、`foreign_keys=ON`、`journal_mode=WAL`、`synchronous=FULL`、显式 `BEGIN IMMEDIATE`；**不要**依赖 FTS5（stdlib 构建不保证），启动时 `PRAGMA compile_options` 探测后可选启用 |
| `store.py` | 内容寻址 blob 存储 | `store/sha256/ab/cd/<hash>`；写临时文件 `O_EXCL` → fsync → `os.replace`；目标已存在则校验哈希后复用，**绝不覆盖**；读时复算哈希 |
| `probe.py` | 媒体嗅探（可插拔 `MediaProbe` 协议） | 默认 `StdlibProbe`：magic bytes 判容器（mp4/mov/webm/mkv/png/jpeg/gif）+ 解析 MP4 `ftyp/mvhd/stts/mdhd`；**证明不了就返回 `trusted=false`**。可选 `FfprobeProbe`（二进制存在时） |
| `license.py` | claim 规范化、注册表校验、`evaluate(claim, profile)` | 输出理由码，不做"猜测式放行" |
| `review.py` | 审核记录、`subject_digest`、supersede | 只插入，不 update/delete |
| `gate.py` | **单点判定** | `evaluate(asset_id, profile_id, at)`，被 handoff/verify/retrieve 共用 |
| `query.py` | 分镜 → 检索需求 | 白名单/黑名单、PII 拦截、默认拒绝 |
| `retrieve.py` | 分镜需求 ↔ 本地库匹配 | 确定性打分，无外部索引 |
| `handoff.py` | 交付与复核 | 编辑器四字段不改名不加必需项 |
| `audit.py` | 事件日志 + 使用日志 | JSONL 追加 + DB 表双写 |
| `cli.py` | 子命令 | 统一 `--at` 注入时钟，保证可测试 |

## 3. 数据模型（append-only）

```sql
content(content_sha256 PK, size, mime_sniffed, first_seen_at, store_relpath)
probe(content_sha256, engine, trusted, container, codec_v, codec_a,
      fps_num, fps_den, fps_mode /*cfr|vfr|unknown*/, duration_ms,
      width, height, has_audio, raw_evidence_sha256, probed_at)

asset(asset_id PK, content_sha256 -> content, canonical_key NULL, variant_of NULL,
      display_title, display_tags_json, description, created_at)      -- 展示字段，非法定

meta_rev(meta_id PK, asset_id, rev_no, legal_json, legal_digest, display_digest, created_at)
   -- legal_json 只放：author/attribution/license 相关引用/来源 id；改它 = 新行

source_assertion(src_id PK, content_sha256, url, effective_url, site,
                 page_evidence_sha256, evidence_kind /*page_snapshot|operator_assertion|api_meta*/,
                 retrieved_at, retrieved_by, access /*public_no_login|denied|paywall*/,
                 superseded_by NULL)

license_claim(claim_id PK, src_id, license_id, license_version, license_url,
              license_text_sha256, author, attribution_template, attribution_rendered,
              allowed_commercial, allowed_derivative, allowed_distribution_json,
              requires_attribution, requires_notice, sharealike, revocable,
              territory_json, valid_from, valid_until, releases_json, risk_flags_json,
              evidence_rank, superseded_by NULL)

use_profile(profile_id PK, dims_json, profile_digest, created_at)
   -- dims: commercial, derivative, distribution[], territory, term_end,
   --       attribution_delivery, sharealike_ack, policy_version

review(review_id PK, subject_digest, subject_components_json, asset_id, content_sha256,
       basis_claim_id, profile_id, decision /*approved|rejected|needs_info*/,
       reviewer_id, reviewer_kind /*human only for approved*/, checks_json,
       obligations_json, valid_until, notes, created_at, supersedes, payload_sha256)

event_log / usage_log(usage_id, handoff_id, asset_id, review_id, subject_digest,
                      profile_id, output_id, used_at, acted_by)
```

强约束：
- `BEFORE UPDATE/DELETE ON review, license_claim, source_assertion, content, probe` → `RAISE(ABORT,'append_only')`。**改元数据只能插新行**，而新行会改变 digest，旧审核自动失效。
- `STRICT` 表（SQLite ≥3.37，启动探测）+ `CHECK` 约束（decision/reviewer_kind/fps_num 等）。

## 4. 去重 × 多来源许可共存

- 同一字节流 = 一个 `content` 行；重复导入只追加 `source_assertion` + `license_claim` + `event_log`，`duplicate_of` 回填到导入结果里。
- **一条内容可以有 N 个许可主张，但审核永远针对"某个 claim"**，可用权利 = 被审核的那一条，**绝不取并集/最宽松**。
- 多个 claim 都被审核通过时：`gate` 必须显式返回 basis 列表；`handoff` 需 `--basis` 指定，或在 `prefer` 策略下确定性选一条并**写进 handoff 记录**（依据：`evidence_rank` 降序 → `valid_until` 最晚 → `claim_id` 字典序）。事后收到下架通知时能定位到底用了哪条依据。
- 近似重复（重编码）标准库无法可靠判定：只支持操作员显式 `--canonical-key` 建立 `variant_of` 边。**变体默认不继承审核**；确需继承必须走 `link-variant --inherit-rights`，生成**新审核**并强制重跑 probe。这是最容易埋雷的地方。
- 许可漂移：同一 `license_url` 的证据快照哈希变了 → 新 `license_claim` 行，旧审核失效（理由码 `LICENSE_EVIDENCE_CHANGED`）。

## 5. 防旧审核滥用：`subject_digest`

```
subject_digest = sha256(canonical_json(NFC, sorted_keys, no_float)= {
  content_sha256, mime_sniffed,
  probe_fingerprint,                  # container/codec/fps_num/fps_den/duration 等
  src.url + page_evidence_sha256,
  claim 的法定子集,                    # license_id/version/text_sha256/author/attribution/
                                      # allowed_*/obligations/valid_*/territory/releases
  profile.profile_digest,
  review_policy_version
})
```
- 审核行同时存 `subject_components_json`，判定失败时可**指出到底是哪个维度变了**（不是笼统"suspicious"）。
- 展示型字段（标题/标签/描述）进 `display_digest`，**不进** subject_digest；但语义性约束（"仅编辑用途"）禁止放标签里，必须进 `allowed_*`。
- 失效四种情形：`BASIS_NOT_REVIEWED`（新 claim/新来源）、`USE_PROFILE_CHANGED`、`REVIEW_EXPIRED`、`CONTENT_MISMATCH`（blob 被替换/损坏）。
- **`review.reviewer_kind != human` 的记录最多算 `needs_info`**；agent 只能提议，不能放行。

## 6. 用途档案与许可判定

```json
// use_profiles/default_online.json
{"schema":"assetlib.use_profile/v1","profile_id":"up_online_default",
 "commercial":true,"derivative":true,
 "distribution":["web","social"],
 "territory":["worldwide"],"term_end":null,
 "attribution_delivery":"end_card_plus_description",
 "sharealike_ack":false,"policy_version":"lic-v1"}
```

判定（理由码，全部可测）：
| 情形 | 结果 |
|---|---|
| `!allowed_commercial && commercial` | `LICENSE_NC_BLOCKS_COMMERCIAL` |
| `!allowed_derivative && derivative` | `LICENSE_ND_BLOCKS_DERIVATIVE`（注意：剪辑必然改字节，`derivative` 默认 `true`） |
| `distribution` 有任一上下文不在 claim 允许集 | `DISTRIBUTION_CONTEXT_NOT_LICENSED` |
| `sharealike && !sharealike_ack` | `SHAREALIKE_UNHANDLED` |
| `territory` 未覆盖 / 未声明 | `TERRITORY_UNCOVERED` |
| `valid_until < at` | `LICENSE_EXPIRED` |
| `releases.model==false` 或 `risk_flags` 含 `recognizable_minor` | `PRIVACY_HARD_BLOCK`（隐私轴独立于许可） |
| 无任何 claim | `LICENSE_UNKNOWN` ← **剪贴线** |
| 仅有 `LEGAL_EXCEPTION`/fair-use 主张 | `REQUIRES_COUNSEL`，永不进可用队列 |

许可注册表 `licenses/registry.json`（CC/SPDX 常见 id + 默认义务）**只能预填"待确认主张"，不能直接生成可用 claim**；`lint-candidate` 拒绝注册表外的 `license_id`（防拼错造出"更宽松"的假许可）。

## 7. 检索需求生成 + 隐私闸门

- 只从**操作员维护的关键词白名单**里生成查询串；分镜原文只以 `source_field` 指针 + `text_sha256` 出现，**原文不进 plan**（避免私人细节外泄到检索）。
- 默认拒绝：场景未被 `plans/allow_scenes.json` 显式列出、或命中 PII 正则（邮箱/手机/证件/住址）、命中去重名单 → `blocked:true` + 理由码，不生成检索任务。
- 显式非私人标记读取策略：存在就信，缺失就**当作私人**（默认拒绝），由操作员显式放开。

## 8. JSON 契约

```json
// q_*.json
{"schema":"assetlib.query_plan/v1","plan_id":"q_...","generated_at":"...Z",
 "storyboard_snapshot_sha256":"...","policy":{"auto_network":false,"import_only":true},
 "requests":[{"request_id":"q_01","scene_ids":["s01"],"asset_ids":["a01"],
   "need":{"kind":"video","duration_ms":[2000,4000],"must_include":["coffee shop"],
           "editor":{"fps":"exactly_30","fps_mode":"cfr","container":["mp4","mov"]}},
   "keywords":[{"text":"coffee shop morning","source_field":"scenes[0].visual","allowlisted":true}],
   "blocked":false,"block_reasons":[]}]}
```

```json
// candidate.json  (操作员/AI 填写，人工下载后的元数据)
{"schema":"assetlib.candidate/v1","request_id":"q_01",
 "source":{"url":"https://...","site":"...","retrieved_at":"...Z","retrieved_by":"operator",
           "access":"public_no_login","page_evidence_sha256":"..."},
 "file":{"local_path":"imports/a.mp4","declared_mime":"video/mp4"},
 "claim":{"license_id":"CC-BY-4.0","license_url":"https://...","license_text_sha256":"...",
          "author":"...","attribution_template":"{title} by {author} / {license}",
          "allowed":{"commercial":true,"derivative":true,"distribution":["web","social"]},
          "requires_attribution":true,"sharealike":false,"valid_from":"2026-01-01",
          "valid_until":null,"territory":["worldwide"],
          "releases":{"model":null,"property":null},"risk_flags":[]}}
```
`lint-candidate` 强制：`https` only、`page_evidence_sha256` 或显式 `evidence_kind=operator_assertion`、字段齐全、许可 id 在注册表内；缺证据 → 允许入库但**永远无法通过 approved 审核**。

```json
// review 记录（写入）
{"schema":"assetlib.review/v1","review_id":"rv_...","subject_digest":"sha256:...",
 "asset_id":"as_...","basis_claim_id":"lc_...","profile_id":"up_...",
 "decision":"approved","reviewer":{"id":"op-1","kind":"human"},
 "checks":{"license_text_seen":true,"source_page_seen":true,"author_match":true,
           "attribution_ready":true,"privacy_ok":true,"editor_compat_ok":true},
 "obligations":{"attribution_rendered":"...","notice":false,"sharealike":false},
 "valid_until":"2026-12-31","created_at":"...Z","supersedes":null}
```

```json
// out/<handoff_id>/handoff.json
{"schema":"assetlib.handoff/v1","handoff_id":"h_...","at":"...Z","profile_id":"up_...",
 "gate":{"policy_version":"lic-v1","require_human_review":true,"require_probe_trusted":true},
 "assets":[{"asset_id":"as_...","path":"media/as_....mp4","sha256":"...",
   "rights":"cleared",                     // 来自 editor_contract.json 的映射，不自己发明枚举
   "rights_basis":"assetlib:h_...|rv_...|lc_...|CC-BY-4.0|use=commercial+derivative+web,social|exp=2026-12-31|attr=required|ev=sha256:ab..",
   "media_kind":"video","probe":{"fps_num":30,"fps_den":1,"fps_mode":"cfr","engine":"ffprobe"},
   "attribution":"...","review_id":"rv_...","claim_id":"lc_..."}],
 "excluded":[{"asset_id":"as_...","reason_code":"LICENSE_UNKNOWN"},
             {"asset_id":"as_...","reason_code":"EDITOR_FPS_UNVERIFIED"},
             {"asset_id":"as_...","reason_code":"EDITOR_MEDIA_KIND_IMAGE"}]}
```

## 9. CLI

```bash
python -I -m assetlib init --root ./assetlib_data
python -I -m assetlib query-plan  --storyboard sb.json --allowlist kw_allow.json --allow-scenes plans/allow_scenes.json --out plans/
python -I -m assetlib lint-candidate --candidate cand.json
python -I -m assetlib import      --file imports/a.mp4 --candidate cand.json --actor op-1
python -I -m assetlib probe       --asset as_...        # 或 import 时自动，可复算
python -I -m assetlib review      --asset as_... --basis lc_... --profile up_online_default \
                                  --decision approved --reviewer op-1 --checks-file checks.json \
                                  --valid-until 2026-12-31
python -I -m assetlib link-variant --from as_1 --to as_2 --inherit-rights   # 生成新审核
python -I -m assetlib retrieve    --plan plans/q_01.json --profile up_online_default --usable-only
python -I -m assetlib handoff     --plan plans/q_01.json --profile up_online_default --out out/run1 [--basis lc_...]
python -I -m assetlib verify      --handoff out/run1/handoff.json [--at 2026-11-04T00:00:00Z]
python -I -m assetlib audit       --asset as_... | --handoff h_...
python -I -m assetlib to-screenwriter --plan plans/q_01.json --out assets.json   # 最高 available_unverified
```
全部命令无网络。唯一可能的网络入口 `fetch-evidence --url <单个显式 URL>`：走既有 `discovery_agent.net.Fetcher`（白名单/DNS/预算/超时复用）、`--network` 显式开关、**单 URL 不跟随链接**、记录 `access:denied/paywall` 即停、尊重 robots 并把结果落成 `page_evidence_sha256`。MVP 默认关闭。

## 10. 与剪辑 handoff 的硬闸（30fps）

`handoff` 只放行同时满足：`media_kind=="video"` ∧ `probe.trusted` ∧ `fps_num/fps_den == 30/1` ∧ `fps_mode=="cfr"` ∧ 容器/编码在 `contracts/editor_contract.json` 允许集 ∧ `duration` 在场景窗口内 ∧ `gate.usable`。

- 图片、非 30fps、VFR（平均 30 也不算）、容器不明的 → 进 `excluded`，理由码明确。
- **不做隐式转码**，也不写"稍后转换即可用"。需要新素材时回到编剧侧 `to_create`/`to_capture`，而不是伪造 `available`。
- `contracts/editor_contract.json` 声明 `rights` 字段是枚举还是自由文本；映射缺失 → `EDITOR_RIGHTS_UNMAPPED` 排除，**不自造枚举值**。
- `verify` 是同一 `gate` 的再执行：重算文件哈希、重算 subject_digest、检查 `valid_until` 与 `term_end`。渲染前必跑。

## 11. 测试矩阵（unittest，无网络）

| 类别 | 用例 |
|---|---|
| Digest 稳定性 | 键序/Unicode NFC/整数 vs 浮点；同输入字节级一致 |
| 不可变性 | 直接 SQL `UPDATE review` / `UPDATE license_claim` 被触发器 ABORT |
| 旧审核滥用 | 新增"更宽松"的 claim → `gate` 变 `BASIS_NOT_REVIEWED`；改 `page_evidence_sha256` 同效；仅改 tags/标题 → 仍可用（文档化） |
| 去重 | 同文件二次导入：1 个 content、2 个 source、blob 不重写；并发导入 `BEGIN IMMEDIATE` 下无重复 |
| 完整性 | 手工翻转 blob 一个字节 → `verify` 报 `CONTENT_MISMATCH`；`os.replace` 中途崩溃注入 → 无半截 blob |
| 许可真值表 | NC×commercial、ND×derivative、SA 未确认、territory 未覆盖、expired、unknown、LEGAL_EXCEPTION 全枚举 |
| 媒体闸门 | `.mp4` 实为 WebM；截断 moov；VFR 平均 30fps；1080p30 正常放行；png 排除 |
| 隐私闸门 | 未列 allow_scenes → blocked；邮箱/手机号命中 → blocked；plan 中不含分镜原文 |
| 契约漂移 | 用样例分镜/编剧 assets 做金样测试：上游加字段我们忽略、上游删必需字段我们显式失败（而非静默） |
| 无网络 | monkeypatch `socket.socket` 抛错，除 `fetch-evidence` 外全部通过 |
| 路径安全 | symlink/设备文件/超限文件被拒；`--out` 落在 `FORBIDDEN_WRITE_ROOTS` 时报错 |
| 时间 | 全部命令 `--at` 注入；`valid_until` 边界（当日 23:59:59Z 仍有效，次日失效） |

## 12. 应避免的坑

**法律/合规**
1. 把"程序放行"写成"已合规"：`rights` 只能是 `cleared/unverified` 之类的运营标签，`DISCLAIMER` 明确不等于法律意见。
2. 多来源许可取并集 → 实际是"许可证洗白"。必须单一 basis、显式记录。
3. 把 SA/NC/ND 揉进一个 `risk` 字符串 → 无法机检。必须是结构化布尔 + 独立理由码。
4. `derivative` 默认 false（以为"只是剪一下"）。剪辑改字节即衍生，默认 true。
5. fair use / 编辑性使用当许可用。直接 `REQUIRES_COUNSEL` 排除。
6. 只审许可不审隐私：肖像/商标/未成年人独立轴，`PRIVACY_HARD_BLOCK` 优先于一切许可。
7. 许可到期只在导入时看一次。`gate` 每次带 `at` 重算。

**工程**
8. 用可变 `asset.status` 当真相 → 元数据一改，旧审核还在。必须 digest 派生。
9. 按文件名/声明 MIME 判类型；`.mp4` 是 WebM、是图片、是截断文件。
10. 只信容器头报的"30fps"；VFR 平均 30 不等于 CFR 30。
11. 导入时"顺手转码/规整" → 哈希身份没了，证据链断了。导入只存原始字节。
12. blob 塞进 SQLite → 库膨胀、无法流式、备份困难。
13. 变体重编码自动继承审核。
14. 依赖 FTS5 / 外部索引库；stdlib 构建不保证。
15. 用内置 `hash()`、naive datetime、`os.path.exists` 做 TOCTOU 判断。
16. 归一化策略不写进文档 → 半年后 digest 全变，历史审核集体失效。

**协作**
17. 改编剧/剪辑共享文件或往其目录写产物。
18. 把库的审核结果写进编剧 `rights_review` 字段冒充"编剧侧审核"。适配器只能写 `external:assetlib rv_...` 且 `status` 封顶 `available_unverified`。
19. 往 editor 的 `assets` 里加必需字段。只能加可选字段，且 `verify` 要断言四字段（path/sha256/rights/rights_basis）语义不变。
20. 让 AI 生成 URL/作者/许可后直接入库。无 `page_evidence_sha256` 的主张只能停在 `operator_assertion`。

## 13. MVP 边界与里程碑

- **MVP（必须全绿）**：`init / lint-candidate / import / probe / review / gate / retrieve --usable-only / handoff / verify / audit`，import-only，零网络。这已构成"分镜 → 检索需求 → 人工取回 → 审核 → 可复现交付"的完整闭环。
- **M2**：多 claim 共存与 `--basis` 选择、许可证据漂移、`to-screenwriter` 适配、报表。
- **M3（可选）**：`fetch-evidence`（Fetcher 单 URL 证据归档）、`--canonical-key` 变体、FTS5 加速。

## 14. 需 Codex 确认的 3 个开放项（已给推荐默认，不阻塞实现）

1. 剪辑 `assets.rights` 是枚举还是自由文本 → 默认走 `editor_contract.json` 映射，缺映射即排除。
2. 是否允许 `ffprobe` 作为可选外部探针 → 默认关闭，纯 stdlib 时 `trusted` 可能为 false，此时视频**一律排除**（宁可少交付，不伪造可用）。
3. 是否存在"仅内部预览、不商用"的用途档案 → 默认只提供 `up_internal_preview`（commercial=false, distribution=["internal_review"]），与商用档严格分离，防止内部档结果被误用到发布链路。
