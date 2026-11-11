# 本地中文预设配音准备

`creative_agent voice` 为冻结分镜准备一条完整旁白，后续仍由操作者导入剪映、原生导出和听审。
不是声音克隆服务，不调用云端 TTS，不自动取得发布批准。旧系统声音和历史工程保留原样。

本轮 Claude Code 设计调用在执行前被自动审批拒绝：审核器要求该具体项目接口摘要发送至
muskapis 的授权证据。没有执行、没有更换通道重试，也不把本地设计说成 Claude 输出。
依用户此前允许的回退，由 Codex 实施并审核本地方案；公开下载与本机推理是独立工作。

## 选型及许可依据

实际读取的主来源为 [Kokoro 中文模型卡](https://huggingface.co/hexgrad/Kokoro-82M-v1.1-zh)、
[官方中文示例](https://huggingface.co/hexgrad/Kokoro-82M-v1.1-zh/blob/main/samples/make_zh.py)、
[Kokoro 代码许可](https://github.com/hexgrad/kokoro/blob/main/LICENSE)、
[Misaki 许可](https://github.com/hexgrad/misaki/blob/main/LICENSE)及
[Apache 2.0 条款](https://www.apache.org/licenses/LICENSE-2.0)。

- 固定模型：`hexgrad/Kokoro-82M-v1.1-zh`，预设 `zf_001`。
- 固定官方仓库提交：`01e7505bd6a7a2ac4975463114c3a7650a9f7218`。
- 权重：327,247,856 字节，SHA-256
  `b1d8410fa44dfb5c15471fd6c4225ea6b4e9ac7fa03c98e8bea47a9928476e2b`。
- 音色张量：523,331 字节，SHA-256
  `9bdc9a87e13e9bb1ea3e7803259c2ecbfebaeeb2ff80b5d0c76df1a464c1c962`。
- 模型卡将发布模型标为 Apache-2.0，并说明中文训练资料来自获许可的专业数据。
  代码及中文前端相应来源亦保留 Apache 通知。选择这个预设，不使用另三种合成英语音色。

这些是发布者提供的版权许可和来源依据，不是独立的人格权审计、人物代言或生成音频版权所有权证明。
没有发现该预设另附仅限个人非商业的使用条件，因此可据其发布许可评估本项目公开使用；
仍不得冒充真实人物、暗示代言或自动填写整片 rights/privacy 通过。
软件、权重与依赖没有打包分发给视频受众；若将来分发它们，须另遵守相应许可证和 NOTICE。

实际隔离环境保存了 PyPI 安装报告、版本及包内许可清单。核心涉及 Apache、MIT、BSD，
英文 G2P 依赖还包含 GPL 的 phonemizer 等；中文路径未调用英文 fallback，
不能把全部传递依赖都称为 Apache。环境仅供本机运行，没有将包或模型提交 Git。

另核查 [Qwen 0.6B CustomVoice 模型卡](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice)，
其预设及 Apache 标记可作候选，但未下载、未安装或测试，不把 CUDA 示例当作 Mac 已验证路径。
TTSMaker 官方商业条款也是候选，尚未验证具体音色/界面和新协议，不调用其服务。
剪映音色的商用筛选不能替代逐音色费用与范围核查；CapCut 网页说明不能移用为国内剪映许可。

## 显式准备与输入

先在获准的任务目录建立隔离环境，从官方 PyPI 安装可选
[顶层版本](../requirements-local-voice.txt)。不改全局 Python；这些不是完整的传递依赖哈希锁。
另行取得上述固定提交的 `config.json`、`kokoro-v1_1-zh.pth`、`voices/zf_001.pt` 和 `README.md`，
核对 SHA 后再提供本地路径。准备命令本身不下载、不接受 API key，不运行远端模型代码。

```sh
python3.12 -m venv var/local-voice-venv
var/local-voice-venv/bin/python -m pip --isolated install \
  --index-url https://pypi.org/simple -r requirements-local-voice.txt
# 完成固定模型文件的单独下载与核验后：
var/local-voice-venv/bin/python -m creative_agent voice var/voice/plan.json \
  --output var/voice/new-attempt
```

请求为严格 `local_preset_voice/v1`：`schema / id / edit_plan{path,sha256} / repository /
revision / voice_id / files / speeds`。
`files` 必须恰好包含 `weights/config/voice/model_card` 四个 `{path,sha256}`；四项哈希固定在
[后端](../creative_agent/voice.py)，不接受任意 pickle 或替换音色。
`speeds` 显式覆盖冻结脚本每句，整数或有限小数 1..1.35，拒绝 bool/NaN/未知句。
每句恰好一个字幕时段。该受限中文路径拒绝拉丁字母，避免未配置英文前端时静默丢词。

父进程不导入 Torch，用当前隔离解释器启动独立 worker；清除继承的认证环境，
设置 Hugging Face/Transformers 离线和禁遥测参数，使用显式本地配置与张量路径。
CPU 两个计算线程、一个 interop 线程，不启用 GPU/MPS 回退，不改系统权限。
这是 SDK 离线配置，不是操作系统网络或内存隔离保证。
当前固定 Kokoro 0.9.4 的权重加载和显式音色加载均为 `weights_only=True`。

先生成完整音素串，未知字符、空串或超过 510 音素拒绝，不允许管线静默截断。
每句保留原始浮点 WAV 与事实记录；将完整语音放在对应字幕首帧，至少留 80ms 尾部空白。
超过时段、静音、非有限值或越幅直接失败，不截断、不在后台自动调速重试。
操作者查清失败原因后，显式新建请求和 attempt 再试；模型 speed 不是产品实际耗时。
最后只允许统一减小峰值，不自动提升、压缩或变速来冒充改善了听感。

父进程默认 300 秒等待上限，库参数限 1..600 秒；超时/取消只终止、回收本 worker 进程组，
写失败结果并保留样本。输出目录 0700、文件独占创建、拒覆盖/用户符号链接祖先/`..`；
最终旁白生成后重查绑定、格式、准确总时长并全解码，才写 `prepared_not_listened`。
支持同 UID 合作场景，不声称能隔离同 UID 恶意进程。

结果含模型文件哈希、每句时长/语速、运行包版本、输出哈希和解码状态。
`actual_listening_completed / automatic_rights_approval / publication_approved` 均为 false。
命令退出 0 只表示准备成功；转成剪辑素材后用 `provided` 音轨、新工程和新的导出审查。

## 当前 Mac 真实验证

本机隔离安装和实际导入成功，使用上列固定权重/音色在 CPU 运行，没有注册、付费、密钥或媒体上传。
内存容量 sysctl 读取被执行环境拒绝，未重试或推断容量；实际推理成功不能推导资源上限。
第一轮显式 speed 1.15：第三句生成 3.4 秒，预算 3 秒，按设计失败，完整失败语音保留。
第二轮将七个短镜头的模型 speed 显式改为 1.35，其余四句保留 1.15；11 句全部完整容纳，
没有截断。24kHz 单声道总长 36.000 秒、完整解码通过，SHA-256：
`efa04a2e97cecfb6ebfbb6ba239cee47bf973547c17fb1a176c480c75f9acbab`。
这不是自然度、数字读法、感情或语速舒适度的听审结论。

实际剪映替换、成片核验、素材候选与待验项见 [v8 迭代记录](creative-review-validation.md)。
7 项新增测试覆盖固定绑定、未知字段/音色、调速边界、中文限制、整句时段放置、异常音频、
模拟超时，以及不生成语音/不动桌面的真实子进程超时回收。真实神经网络合成另有上述本机证据。
