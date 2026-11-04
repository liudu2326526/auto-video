---
name: source-materials
description: 根据视频分镜检索公开图片/视频/音频候选，逐项记录授权证据，保存与检索本地素材库，并向编剧和剪辑交付经人工复核的素材。
---

# 素材搜集和复用

在仓库根目录阅读 `agents/source-materials.md`、`docs/material-agent-mvp.md`，运行
`python3.12 -m material_agent --help`。助手负责真实公开检索、看素材和复核，helper 只处理本地结构与文件。

1. 从受信分镜为需要外部情境素材的镜头创建 `material_requests/v1`；显式写 public_nonpersonal，
   提供公开检索词、scene/line/asset ID、类型与用途。不要发送原始私密分镜到搜索引擎。
   本岗位不得把第三方图片/宣传片当作产品实测证据。
2. `plan --requests <file> [--script <writer-draft>]` 先检索本地库；按返回的 blocker 判断是否需要
   重新核验、下载或制作。已用过、文件相同或许可名称相同，都不代表当前用途已经获准。
3. 只用 public_queries 通过当前可用的联网工具检索；不假定平台 API 可用。打开单个素材页和
   原始许可页面，保存 asset_license 和 license_terms 两种证据摘要及检索时间。
   无法访问时如实记 blocker；不绕过登录/付费墙/条款或通过其他入口规避拒绝。
4. 用 `add --record <metadata>` 保存候选。正常、允许的公开下载由操作者执行，取得文件后用
   新记录 ID 加 `--file <original> --download-record <receipt>` 导入副本。MVP helper 不发网络请求。
   来源未知的旧素材可入库，许可保持 unknown，不能交剪辑。
5. 实际看文件、核对许可适用范围、字幕内容和第三方权益后，填写独立 `material_review/v1`，用
   `review --receipt` 留存操作者、时间、用途、条件与风险处理事实。不能预写通过或冒充用户审核。
6. `search --query <public terms> --context <json>` 查看复用资格。
   用绑定 requests_digest 的 selection 运行 `handoff`，交付具体文件、署名、来源与未完成镜头。
   不自动填补产品结果证据，不把素材包说成成片。图片/音频等格式转换另行处理。
7. 剪辑实际使用之后才用 `used --receipt` 记工程、镜头和逐句引用。通过 `show --id` 查询历史。
   运行时原文件副本、SQLite、使用日志与交付包只放忽略的 var/ 或仓库外，不加入 Git。
   在实际剪辑前运行 `verify --handoff <materials.json>`，重查本次交付的哈希、署名、当前许可与审核。
