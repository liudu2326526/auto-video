# 单文件公开素材下载与入库

本页描述原Commons下载入口。其他已接来源见 [Pexels/Pixabay、Noto及Mixkit目录](material-providers.md)，不放宽本入口域名或许可白名单。

现可显式执行 download。其他 plan/search/add/review/handoff/verify/used/show 命令离线。
本次没有登录、读取 Google 会话、保存凭据、上传素材或发布视频。

## 使用和范围

在项目根目录运行，需 Python 3.12、ffprobe、ffmpeg：

```sh
python3.12 -m material_agent download \
  --record fixtures/materials/commons-laptop-download.json \
  --policy fixtures/materials/commons-download-policy.json \
  --context fixtures/materials/use-context.json

python3.12 -m material_agent downloads --id commons_laptop_download_v1
python3.12 -m material_agent show --id commons_laptop_download_v1
```

download 是明确的单文件执行入口；plan 不联网、不执行下载。默认库仍为忽略的 var/material-library。
已有元数据候选不能被补写/覆盖，下载用新记录 ID。相同 ID、相同元数据和已验证文件再次执行时，
校验本地哈希及当前许可/用途后返回 reused_local、network_requests=0。
不同记录相同内容只复用 blob，不继承其他来源的许可或人工批准。

- 仅支持 Commons 的公开原始文件，固定 upload.wikimedia.org 和原始路径形状；素材页和下载文件名必须对应。
  拒绝查询参数、IP、凭据、非 HTTPS、特殊端口、编码混淆路径、缩略图/存档及未知域。
- 许可须逐文件核查：CC0 1.0、CC BY 4.0、CC BY-SA 4.0、官方许可 URL、素材授权证据及适用用途。
  未知/受限许可不能下载，robots 允许不等于版权许可。
- 支持 JPEG、PNG、WebP、MP4、WebM、Ogg、WAV、MP3。响应 MIME、魔数、探针与实际解码必须一致。
  不支持 GIF、SVG、HTML、压缩包、可执行、通用二进制 MIME、续传或批量下载。
- 每文件最多32MiB（示例4MiB）；网络总期限5–120秒（示例60），连接/读取各最多15秒且受总期限约束。
  探针和解码各最多15秒；视频/音频最长10分钟，图像最多4000万像素。
- 一名本机用户同时最多一个下载，媒体流限速1MB/s，不自动重试。
  最多两次媒体重定向，仅同域、同文件名和合法原始路径；下一跳先过 robots。
- UA 明确程序、版本和公开项目联系网址，不伪装浏览器，不发送 Cookie、Authorization 或私人邮箱。

政策 material_download_policy/v1 包含 provider=commons_original、record_digest、terms_checked_at、
三项官方 terms_evidence（URL/检索时间/摘要）、expected_sha256、max_bytes、total_timeout，可选 resolver。
它是操作者维护的受信本地 JSON，不从网页指令构造。record_digest 绑定整个来源/许可记录。
示例 expected_sha256 为本次实际文件哈希；自己的新素材可先为 null。哈希只证明字节身份，不证明许可真实。

## DNS、robots 与访问拒绝

默认 resolver=system：验证全部 DNS 结果为公网，连接固定到已验证地址，TLS/SNI 验证原始主机。
本机实测系统返回198.18.0.45，公共IP检查正确拒绝，没有将其加入白名单或关闭校验。
此类 fake-IP 环境可显式选用已提供的另一个政策：

```sh
python3.12 -m material_agent download \
  --record fixtures/materials/commons-laptop-download.json \
  --policy fixtures/materials/commons-download-policy.doh.json \
  --context fixtures/materials/use-context.json
```

cloudflare_doh 仅向固定公网1.1.1.1:443建立验证cloudflare-dns.com的TLS连接，查询固定upload域名的A记录。
只发送公开媒体域名，不发送分镜或私人数据。验证唯一问题、状态、截断/禁用验证标记、有界CNAME链及全部公网IPv4，
再固定原站地址、验证upload的TLS。仅本次有界操作内缓存；失败不回退、不改系统DNS、不切换下载入口。
系统模式失败也不会自动启用DoH。参考[官方JSON接口](https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/dns-json/)
和[公开地址](https://developers.cloudflare.com/1.1.1.1/ip-addresses/)。

每次网络下载先查upload的robots，最多64KiB，不跟其重定向。403/404/429/5xx、压缩、无法读取及畸形规则都停止。
当前仅支持一个通用User-agent和字面量Disallow前缀；空Disallow是无操作。遇Allow、通配、锚点、编码路径、
多UA组或其他不支持语义均停止待审，避免stdlib首条匹配误放行。未知域连robots也不请求。
站点拒绝后不换域、换UA、加Cookie或登录绕过。

已核查[Robot policy](https://wikitech.wikimedia.org/wiki/Robot_policy)、
[User-Agent政策](https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy)、
[Commons复用说明](https://commons.wikimedia.org/wiki/Commons:Reusing_content_outside_Wikimedia)。
不自动请求被禁止的 `/w/` oldid 许可页；本次保留此前许可观察，不刷新其日期。

## 入库与审计

流式写入库内0700临时目录，不采用服务端文件名。校验大小、MIME、魔数、SHA256和实际解码后，
以原字节入库，不裁剪或转码。失败清理临时文件，不覆盖已有blob。来源记录、blob索引与成功审计同事务；
数据库/成功审计故障一起回滚，并清理新blob。

acquisition.method=automated_public_download，verification 保留实际URL/重定向链、robots哈希/时间、
字节数、SHA256、类型/解码、用途、政策摘要、解析模式、请求数与UA。
追加式 download_events 保存 started/downloaded/reused_local/failed。
downloads --id 可查失败后未入库的尝试；不记录错误正文、Cookie、带密码URL或任意异常详情。
下载不自动写human approved或used，不生成产品实测证据。后续仍需用途审核、handoff/verify；
图片需另行制作成镜头，不能冒充30fps视频。其余协议见[素材库说明](material-agent-mvp.md)。

## 真实验证和设计来源

实际下载[ Laptop image.jpg](https://commons.wikimedia.org/wiki/File:Laptop_image.jpg)，Darakshanehaluddin，
依据已有[CC0证据](https://creativecommons.org/publicdomain/zero/1.0/deed.en)，许可观察仍为2026-11-04T08:31:11Z。
DoH、robots、媒体共3个HTTPS请求；JPEG 1600×1200、193728 bytes、解码通过：

`SHA256 c35d650df80513bf822601807a236f43c2a73df4c27a4b669cf37b475b1c0cbe`

SHA-1与此前素材页记录相同。实际看图有ASUS等品牌标识，不自动认定适合广告或任何发布用途。
真实验证还覆盖零网络本地复用、错误缓存校验和、64字节限制在媒体响应头阶段阻断、无半文件、无伪造剪辑使用记录。
原图、SQLite、完整收据仅放忽略目录或仓库外，不入Git。

```sh
python3.12 -m unittest discover -s tests -p 'test_material*.py' -v
python3.12 -m unittest discover -s tests -p test_net.py -v
```

设计优先实际调用Claude Code；[原始提案](material-download-claude-proposal.md)经Codex审核后实施。
采用许可前置、逐跳robots、隔离校验、去重、同事务审计和不继承批准。
未改共享discovery_agent.net，复用公共IP/DNS pin/TLS原语，新增单跳流式控制以满足前置robots和可联系UA。
独立审查修复了robots误放行、WebM/Ogg冒充图片、成功审计假失败，并补相应回归。
提案中的默认dry、通用provider框架、分级退出码等未实施；本命令明确执行，统一错误退出2。

不支持自动搜索、任意站点、付费购买或自动账号登录。Google登录只在确知具体服务需要时，经安全登录或用户接管处理；
泛泛的登录授权不代表允许授予第三方文件/邮箱权限、保存密码/会话或绕过访问拒绝。本次无需登录。
