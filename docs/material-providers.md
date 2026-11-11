# Pexels、Pixabay 与小型音效/动效目录

本次新增官方API按镜头检索、24小时缓存、候选/许可记录和显式单素材入库；Noto动画有真实下载验证。
没有创建账号、API key、OAuth或长期凭据配置，没有使用Google登录。当前进程未提供两家API key，
因此Pexels/Pixabay仅完成mock验证，不能声称已真实连通。Mixkit保持人工获取。

## 检索与下载接口

```sh
python3.12 -m material_agent provider-status
python3.12 -m material_agent provider-search \
  --request fixtures/materials/providers/pexels-office-request.json \
  --output var/provider-search/<new-attempt>
python3.12 -m material_agent provider-search \
  --request fixtures/materials/providers/pixabay-office-request.json \
  --output var/provider-search/<another-new-attempt>
python3.12 -m material_agent provider-download \
  --candidate-id <registered-id> --variant <returned-rendition-id> \
  --context fixtures/materials/use-context.json
```

默认系统DNS；如环境使用fake-IP，可对上述网络命令显式加 `--resolver cloudflare_doh`。
只查询代码中已审核的固定API/CDN主机，所有地址仍验证公网并固定到TLS连接，不自动切换解析方式。

请求 schema=material_provider_request/v1，包含 id/provider/project_id/scene_id/asset_id/line_ids、
public_nonpersonal=true、query/media_type/limit/orientation/purpose。示例是独立的公开情境镜头，
不冒充当前冻结的Gamma分镜。只能第一页、1–5候选；无翻页采集、下载全部、后台刷新或图库镜像功能。
查询规范化后最多100字符，只允许显式公开词；联系方式、URL、长数字及明显凭据形式被拦截。
此检查不保证自动脱敏，操作者不得把原始私密分镜或业务数据发给API。

搜索导出 candidates.json 和带来源链接/作者的 candidates.md。
候选绑定公开请求、API时间、标准许可/条款摘要和单项来源；未知大小/fps保留未知，不冒充探针观察。
API响应不是工具实测证据，也不证明人物、品牌或财产权利已获授权。
选定ID从库内不可覆盖的候选读取，不能用随意编辑的JSON伪造API来源。
缓存或候选过期后必须显式重搜；不自动刷新可能失效的下载链接。

## 凭据与两种传输角色

仅从当前进程的 PEXELS_API_KEY / PIXABAY_API_KEY 读取既有key，不接受命令行key，不写.env、数据库或Git。
缺key在网络前停止。不要把key贴到聊天、命令参数、截图或提交中；如需创建或长期保存，应先确认具体服务、
权限和保存方式，再经安全登录/用户接管。泛泛的Google登录许可不是创建key或扩大第三方权限的许可。

- Pexels：仅向 api.pexels.com 的固定API路径发送原样 Authorization key，不加Bearer。
- Pixabay：仅向 pixabay.com/api/ 或 /api/videos/ 的官方认证query注入key；私有URL不进入输出、缓存或异常。
- API拒绝重定向，不读取错误正文。密钥回显、畸形JSON或敏感参数在存储前拒绝。
- CDN角色无认证字段，不读取key，不发送Cookie/Authorization；禁止API路径、敏感query和未知媒体域。
  DoH也不携带key。ffprobe/ffmpeg子进程仅继承执行、语言和临时目录设置，不继承父进程凭据。

认证/访问拒绝会持久暂停；429或remaining=0按有效reset和Retry-After中更晚时间暂停。
坏配额头不会解除拒绝。没有自动重试、换key/域/代理规避，未知冷却保守停止，需与服务方解决。
本地额外上限每provider每小时20次API尝试；缓存命中不产生API请求。此上限不替代账户实际配额。

## 两家API的现行约束

核验日期2026-11-04；政策证据超过30天须重新审查，不能因取到新API条目就刷新旧许可条款日期。

| 项目 | Pexels | Pixabay |
|---|---|---|
| 图片 | /v1/search | /api/ |
| 视频 | /v1/videos/search | /api/videos/ |
| 缓存 | 官方建议约24h；本客户端执行24h | 官方要求查询缓存24h；webformatURL有效24h |
| 配额 | 默认200/h、20000/月；Reset是epoch | 默认100/60s；Reset为剩余秒数 |
| 许可 | Pexels License，不能泛称CC0 | Pixabay Content License，不能由路径日期推定CC0 |
| 搜索展示 | 显著链接Pexels，尽可能作者/素材页信用 | 标明Pixabay来源，保留作者和素材页 |

Pexels官方新文档已把视频前缀迁至 /v1/videos/，旧 /videos/ 将弃用。
当前官方[API首页](https://www.pexels.com/api/)和[申请说明](https://help.pexels.com/hc/en-us/articles/900004904026-How-do-I-get-an-API-key)
表明新key发放暂停；不能承诺现在能新申请。已有key是否可用仍需实际验证。

CDN范围故意不完整：Pexels仅 images.pexels.com、static-videos.pexels.com；Vimeo和其他未审核视频域被记录为blocked，
不因API返回链接就放行。Pixabay仅已审核 pixabay.com/get/ 与 cdn.pixabay.com 的JPEG/MP4路径。
所有链接仍需逐跳公网/TLS/robots/MIME/大小/解码检查；原站拒绝或不支持的robots语义就停止。
API调用使用官方程序接口权限，不以网页抓取代替认证API。已知媒体CDN的robots为404/410时按RFC9309处理，
不等于版权授权；403/429及不可解释的规则仍阻断。

官方依据：[Pexels文档](https://www.pexels.com/api/documentation/)、[API缓存建议](https://help.pexels.com/hc/en-us/articles/900006470063-What-steps-can-I-take-to-avoid-hitting-the-rate-limit)、
[Pexels条款](https://www.pexels.com/terms-of-service/)、[Pixabay文档](https://pixabay.com/api/docs/)、
[Pixabay条款](https://pixabay.com/service/terms/)、[许可摘要](https://pixabay.com/service/license-summary/)、
[RFC9309](https://www.rfc-editor.org/rfc/rfc9309.html)。
Pixabay排除的是平台展示的第三方广告/赞助素材，不是把普通Pixabay素材的所有广告视频用途一概禁止。

## 存储、复用和撤回

var/material-library/provider-query-cache.sqlite3 仅存去密钥、裁剪后的API元数据，24小时到期才重新请求；
库内独立表保留不可变候选、查询/作者/许可来源、所选版本、配额与下载历史。
不缓存原始API URL、key、用户头像或整个图库。项目选中的原始媒体仍由既有SHA256 blob库去重，原文件保留。
同一内容来自其他记录也不继承人工批准。media record仍为原严格schema1，provider_imports单独关联来源。
搜索/下载mock标记为mock，不能通过真实CDN角色发起下载；mock只用于临时测试库。

入库、来源关联及成功审计同事务，失败回滚新记录/新blob。查看 `show --id <record-id>` 可见实际来源关联。
取得文件后仍需用途、第三方权利、署名/改动说明及画面人工复核；没有自动human approved或used。
已有 [handoff/verify](material-agent-mvp.md) 继续检查字节、用途和审核，不会把图片/GIF假装成已兼容的30fps视频。

遇适用的侵权/撤回通知，先阻断同provider素材及已入库版本：

```sh
python3.12 -m material_agent provider-withdraw --provider pexels --asset-id <provider-id> \
  --operator <actual-operator> --reason <verified-notice-and-action>
```

该命令阻止以后下载/复用/handoff，保留审计；**不代表已完成删除**。
操作者仍须按通知移除库、原生工程、导出与备份中的适用副本，检查共享blob其他合法依据，不静默删除用户文件。
本次没有收到真实通知，也没有真实Pexels/Pixabay媒体可撤回。

## Mixkit：人工候选，禁止自动获取

```sh
python3.12 -m material_agent source-catalog --source mixkit
```

已保存 Select click 音效与 Tech House vibes（Alejandro Magaña）音乐的列表页、标题、作者/未标明作者、
许可及使用范围。没有假造单项permalink/provider ID或下载URL，也没有下载音频。
Mixkit条款纳入Envato AUP/Fair Use，禁止未经明确授权的自动访问/检索/下载，少量也不例外。
即便有Google登录或目录标记被改，catalog-download也硬性拒绝Mixkit。
用户可按服务允许的普通人工方式取得素材，再核对准确条目、实际文件及许可，以新记录走add；本目录保持restricted/manual-required。
音乐许可列明TV、游戏、独立音乐分发和Content ID等限制，不可把“可商用”当成任意用途许可。

依据：[Mixkit Terms](https://mixkit.co/terms/)、[Envato AUP](https://help.elements.envato.com/hc/en-us/articles/31035788503321-Acceptable-Use-Policy)、
[Fair Use](https://help.elements.envato.com/hc/en-us/articles/360000621743-Fair-Use-Policy)、
[音效许可](https://mixkit.co/license/modal/sfxFree/)、[音乐许可](https://mixkit.co/license/modal/musicFree/)。

## Noto：两份真实动画原件

```sh
python3.12 -m material_agent source-catalog --source noto
python3.12 -m material_agent catalog-download --id noto_thinking \
  --context fixtures/materials/use-context.json --resolver cloudflare_doh
python3.12 -m material_agent catalog-download --id noto_party \
  --context fixtures/materials/use-context.json --resolver cloudflare_doh
```

只支持已逐项核查的官方512px GIF：Thinking Face、Party Popper。官方动画为 **CC BY 4.0**，
不能套用Noto字体OFL或静态图Apache。保留Google / Google Fonts、来源、许可与实际改动说明，不暗示背书。
素材来自可变latest，保存实际SHA256、响应Last-Modified和下载时间，不伪称固定commit。
目录保留初次核查时的风险；实际字节/全帧验证结果在入库verification/provenance。

| 实测原件 | 字节 | 帧数 | 时长 |
|---|---:|---:|---:|
| Thinking Face | 1,388,932 | 60 | 1.800s |
| Party Popper | 395,755 | 42 | 1.380s |

已全部帧探测并完整解码，不只读首帧；再取用零网络。保留原GIF，不隐式转码或声明剪辑兼容。
SHA256分别为 cc4b16879b5f7264a37d7000783776b817b2f8f9e8084672c01d0849ab1bd27c 和
86879c45175f3901a8676d9b0bb5132c7a98b20a9f40487c21e4c896ce196616。
依据：[官方现行页/FAQ](https://googlefonts.github.io/noto-emoji-files/)、
[官方发布说明](https://developers.googleblog.com/updates-to-emoji-new-characters-new-animation-new-color-customization-and-more/)、
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/legalcode.en)。

## 设计与验证

优先实际调用Claude Code；[只读提案](material-providers-claude-proposal.md)经Codex审核后实施。
采用API/CDN凭据分离、独立响应缓存、单页少量请求、来源冻结、GIF完整验证和既有用途审核。
没有照搬过重的独立审批令牌框架，没有修改编剧/剪辑/Gamma文件；纠正提案对Noto动画Apache的假设。
不启用生产localhost例外来测试，网络测试使用受控合成公网DNS/HTTP响应。

```sh
python3.12 -m unittest discover -s tests -p 'test_material*.py' -v
python3.12 -m unittest discover -s tests -p test_net.py -v
```

验证包括API认证隔离与canary不落盘、24小时缓存/配额/超时、候选过期和不可变、撤回、真实生成JPEG/MP4/GIF解码、
来源/审计失败回滚、Noto真实原件及Mixkit零网络阻断。完整回归另有既存AVFoundation录屏环境失败，不能据此报告全绿。
当前需用户操作：安全提供已有API key后再做真实API验证；创建key、OAuth或长期保存均须另行确认。
