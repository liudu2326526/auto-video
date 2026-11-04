# 素材 Agent 与本地素材库 MVP

新增 [Pexels/Pixabay官方API、Mixkit人工目录与Noto动画](material-providers.md)；原有素材库协议和人工用途审核继续生效。

这是可被父助手反复调用的素材岗位与本地 Python helper。助手用当前可用的公开网页工具实际检索、
看素材和核查授权；现可按[公开单文件下载协议](material-download.md)显式执行 download；
其他命令离线，不调用模型或搜索 API。
所有新增文件独立于编剧和剪辑模块，未改共享入口。

## 当前闭环

`分镜的公开素材需求 → 本地检索 → 必要时人工联网检索 → 候选入库 → 正常下载后本地导入 →
实际人工看素材和权限复核 → 按用途复用 → 编剧/剪辑交付 → 实际使用记录`。

默认库在已被仓库根 `.gitignore` 忽略的 `var/material-library/`：

- `library.sqlite3`：不可覆盖的来源记录、授权证据摘要、审核、下载/导入日期和实际使用历史。
- `blobs/<SHA前缀>/<SHA256>.<原扩展名>`：导入文件的副本；原文件不移动、不修改。相同内容只存一份。
- 相同内容的多个来源各有 record ID 和授权，不合并许可，也不继承另一来源的审核。

可通过全局 `--library <path>` 使用持久库；仓库内仅允许 var/，仓库外可使用受权本地目录。
没有常驻服务、后台监听、向量模型、云同步、自动清理或多写入者并发保证；备份须同时保存 SQLite 和 blobs。

## 试跑公开例子

```sh
python3.12 -m material_agent add --record fixtures/materials/commons-laptop.json
python3.12 -m material_agent plan --requests fixtures/materials/gamma-requests.json
python3.12 -m material_agent search --query '电脑 office' --context fixtures/materials/use-context.json
python3.12 -m material_agent show --id commons_laptop_20261104
```

例子只录入实际核查到的公开元数据；没有下载、视觉检查、人工通过或实际使用。
检索会命中它，同时显示缺本地文件与待审核，无法 handoff。重复 add 同 ID 会拒绝，避免刷新旧许可日期。
分镜示例是独立的情境镜头，不冒充当前 Gamma 拍摄方案；实际请求可用 `plan --script <draft.json>`
校验 scene、line、asset 引用，不能把示例镜头 ID 直接塞进现有分镜。

公开例子：[Laptop image.jpg](https://commons.wikimedia.org/wiki/File:Laptop_image.jpg)，
Darakshanehaluddin。2026-11-04T08:31:11Z 检索的
[固定素材修订页](https://commons.wikimedia.org/w/index.php?title=File:Laptop_image.jpg&oldid=1217142170)
在文件自身 Licensing 栏列出 CC0 1.0；
[CC0 官方条款](https://creativecommons.org/publicdomain/zero/1.0/deed.en)
支持版权层面的商业使用和修改，无强制署名。素材页元数据为 1600×1200 JPEG、193728 bytes；
这些不是本地验证结果。CC0 不涵盖商标、隐私/肖像权，也不代表背书；ASUS 标识及屏幕内容尚未看图复核。
该图片只作电脑办公氛围，不证明 Gamma 的生成能力。

## 协议与人工职责

`fixtures/materials/` 有可运行的 metadata、requests、context JSON。严格拒绝未知字段和重复 JSON key。

`material_requests/v1` 有 project_id、public_nonpersonal=true、use_context 与 requests；每项包含
id、scene_id、line_ids、asset_id、description、public_queries、media_types、tags、role。
role 固定 illustrative_context。只以显式 public_queries 搜索公开网络，本地按标题、描述、标签匹配；
助手自行排序相关性，程序不理解图片语义。plan 输出 requests_digest、候选与 blocker，不执行网络。

`material_record/v1` 的 source 包括 provider/page_url/creator/retrieved_at/access/download_url。
正常公开检索使用 access=public_no_login 和确切 page_url；来源已经遗失的历史本地文件可使用
access=unknown_local_history、page_url/download_url=null，rights.status 必须保持 unknown。
这样可以先保存旧素材、标签和使用历史线索，不必杜撰来源；它仍不能通过使用审核或交给剪辑。
rights 有 status、license_id、license_url、commercial_use、derivatives、attribution_required、
attribution_text、restrictions、checked_at、evidence、allowed_distributions、valid_until、share_alike_required。
是否商用/修改必须明确 allowed/prohibited/unknown；署名要求可以 true/false/null。
allowed_distributions 列明 internal/public_social/paid_advertising（null 表示未知），
valid_until 为确切时间或 null，share_alike_required 为 true/false/null。分发范围未知、到期或未知共享义务都会拦截。
known 至少有单个素材授权与许可条款两个 scope 的 URL、检索日期和证据摘要。
证据摘要作为操作员保存的文字记录跟随不可变 document 摘要，不能证明页面真实性；
MVP 不保存完整网页截图/PDF 或外部网页哈希，也不自动联网刷新。
unknown/restricted 可以保存和检索，不能通过使用审核。更正元数据、重新核验许可或补导入文件时创建新 ID。

导入命令：

```sh
python3.12 -m material_agent add --record <new-record.json> --file <original-file> --download-record <download.json>
```

download JSON 为 `downloaded_at`、`method`、`source_url`；method 仅接受
manual_public_download 或 previous_local_material，source_url 必须是本记录素材页或下载地址。
只有 unknown_local_history 的 previous_local_material 允许 source_url=null。
downloaded_at 是操作者声明，imported_at 是入库时间。历史旧素材也需要如实填写已有来源与权限。
无登录的公开页面只表示可访问，不意味着站点准许自动抓取；download只访问支持的公开单文件及必需的robots，
不支持登录下载或付费授权。
公开下载前须由助手/操作者核查站点条款和路径。不要调用去水印服务来推定复制授权。

`material_review/v1` 必需字段：schema、record_id、record_digest、file_sha256、reviewer、reviewed_at、
expires_at、use_context、decision、conditions_acknowledged、risk_review、visual_review、reviewer_kind。
通过 show 获取确切摘要和文件哈希；操作者实际看素材后填写，不提供自动 approved 模板。
decision 是 approved/rejected；reviewer_kind 为 human/agent，只有实际 human 审核能批准。
商业与修改 bool，distribution 为 internal/public_social/paid_advertising，
role 为 illustrative_context。review 绑定精确用途；相同用途可跨项目复用，广告等新用途需要新审核。
use_context 还须明确 share_alike_compatible bool；需要相同方式共享的修改用途不能在其为 false 时批准。
批准时须确认所有 restrictions，人工文字解释人物、商标、屏幕、署名及具体使用场景；
公开检索过素材并不能代替视觉审核。拒绝记录优先于此前同用途批准；后续真实复审可再写新记录。

```sh
python3.12 -m material_agent review --receipt <actual-review.json>
```

每次复用、handoff 和 used 检查哈希、完整元数据摘要、当前用途、最新审核、许可核验日期。
许可和审核最长 30 天，审核期限不得超出许可核验后的 30 天；这只是保守的本地新鲜度规则，
不保证网页许可未变，也不会把文字中的特殊期限自动解析为授权；结构化 valid_until 会限制审核有效期。
操作员应设置更早 expires_at，
有地区、行业、广告、平台、撤销等条件时重新核验。没有真实网络核验不得伪造 checked_at。

## 交给编剧和剪辑

selection 为 `material_selection/v1`：requests_digest 绑定原请求，selections 由
request_id、record_id、operator_notes 组成，可部分满足请求。哈希用 `material_agent.core.digest(requests)`。

```sh
python3.12 -m material_agent handoff --requests <requests.json> --selection <selection.json> --output var/material-handoffs/<new-attempt>
```

新目录包含：

- media/ 素材副本和 materials.json：scene/line/asset 关联、来源、完整授权、风险、精确人工审核、SHA256、缺失请求。
- writer-assets.json：匹配编剧 assets 字段；status 始终 available_unverified，不伪造实测、逐句画面观察或冻结状态。
- editor-assets.json：仅提供通过基本 30fps 检查的视频 asset 映射，字段 path/sha256/rights/rights_basis；
  rights=cleared 只代表本记录已作本次人工用途审核。必须保留 materials.json 和 attribution.txt。
- attribution.txt：保留作者/素材页/许可与署名文案，剪辑者负责满足最终成片及平台的实际展示要求。

handoff 是可审核的素材交接，不会自动合并/覆盖分镜和剪辑计划。将 editor-assets 合并到新的
jianying_edit_plan/v1，路径相对于 handoff 根；如果移动到另一计划目录，必须重定位路径。
editing_agent 还会校验源帧、完整 CFR 时间戳、逐句映射和总时间等，本模块的格式预检不能替代它。
图片、音频与非30fps视频仍会交付并标记 requires_media_preparation=true，不伪装成兼容视频；
当前不裁剪、不转码、不自动生成图片定格镜头或音效轨。网页插图不能冒充工具操作/结果证据，
不自动生成编剧 media_catalog 的观察片段；需要操作者看视频并回填准确区间。

剪辑前使用原库再次核验；verify 会重查最新许可、审核期限及所有交付文件（包括署名）的哈希，
拒绝未登记或改过的 materials.json：

```sh
python3.12 -m material_agent verify --handoff var/material-handoffs/<attempt>/materials.json
```

verify 不写 approved 成片状态，也不替代 editing_agent 验证及实际视听审核。

实际剪辑使用之后填写 `material_usage/v1`：schema/id/record_id/record_digest/file_sha256/project_id/
scene_id/line_ids/use_context/used_at/operator/edit_reference，运行 `used --receipt`。
`show --id` 可查 provenance、下载、审核和使用记录。handoff 本身不记已使用；没有工程操作就不能填 used。
历史使用不可覆盖；未来日期或审核有效期外使用拒绝。过期后的补录/历史迁移暂不支持，不能改时间绕过。

## 验证与边界

```sh
python3.12 -m unittest discover -s tests -p 'test_material_agent.py' -v
```

专项覆盖去重且授权分离、原文件保留、未知许可/缺素材拦截、元数据与文件绑定、用途变化、到期、
最新拒绝、篡改、分镜引用、署名/交付、实际使用历史、符号链接、CLI 公开元数据例子，另以 FFmpeg
生成合成 30fps 视频验证剪辑 asset 预检与现有 editing_agent.validate 字段兼容（无 FFmpeg 时该项跳过）。
测试的审核是合成 fixture，绝非实际素材获准。

SQLite 与文件操作是单操作者 MVP，不承诺应对恶意并发写入或直接篡改数据库；摘要防误用，不是签名/权限系统。
目前没有任意站点/批量下载、全文/向量检索、网页授权变化监控、商业素材购买、自动肖像/商标识别、自动修图或最终成片验收。
