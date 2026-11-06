# 更新日志

本项目的所有重要变更都会记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

[English](../CHANGELOG.EN.md)

## [Unreleased]

### Added

- **token_shark 模式（校园 LLM 网关）**：`./codex-switch.sh token_shark`（或双击 `切换到Token鲨.command`）。切换前从 macOS 钥匙串读 `codex-token-shark`（取不到时回退到配置里内联的真实密钥），再依次做网关预检（`GET {base_url}/models`，要求 HTTP 200 且响应体是 JSON）与协议探测（`/responses` 还是 `/chat/completions`，据此决定 `wire_api`），预检不过就中止且原配置一个字都不动——网关不通比额度耗尽更糟。密钥只存钥匙串，不进仓库也不进模板，切换时才注入 `config.toml`（600 权限）；`~/.codex/token-shark-models.json` 存在时自动写入 `model_catalog_json`。会话 provider 标记按通用逻辑刷成 `token_shark`
- **`CODEX_SWITCH_TOKEN_SHARK_BASE_URL`**：临时换网关地址不必改仓库模板

### Fixed

- **Token鲨 接口地址改为官方对外入口 `https://llmv1.ahau.edu.cn/v1`**：学校《开通通知》《大模型公共服务平台使用手册》《常用模型接入工具使用说明》三处一致写明「平台固定接口地址为 https://llmv1.ahau.edu.cn/v1」。此前用的 `http://llm.ahau.edu.cn:4000/v1` 取自平台接口 `/api/me/api-base-url/`，那是**平台后端内部**的网关地址，学生侧直连会被校园网准入策略丢包（TCP 握手无应答），在 80/81/8080 上则返回带 `Client IP` 的自定义拦截页，看着像服务挂了其实是网络策略。443 端口上的 `llmv1.ahau.edu.cn` 直连可用（uvicorn + vLLM），`/v1/models`、`/v1/chat/completions`、`/v1/responses` 均实测 200，Responses 端点原生支持 `tools` / `parallel_tool_calls`
- **模型目录生成器支持 `max_input_tokens`**：Token鲨 的 `/v1/models` 返回的是 `max_input_tokens` 而不是 `context_window`，此前生成器取不到上下文长度，目录里 `context_window` 为空
- **第三方网关不再被 `custom_tool_call` 卡住**：Codex 的自由文本型工具（`apply_patch`、`exec`）在 Responses API 里的调用记录是两种特殊 input item 类型 `custom_tool_call` / `custom_tool_call_output`。OpenAI 官方认，但很多第三方网关（Token鲨 背后的 litellm 代理等）只认标准 `function_call`，回放老会话直接 HTTP 400：`Unsupported input item type: custom_tool_call (parameter=input)`——所以「新会话正常、老会话一打开就报错」。生成器不再往目录里写 `apply_patch_tool_type`：Codex 0.168 的该字段只认 `freeform`（填 `function` 会让整个目录解析失败），删掉字段则 Codex 根本不注册 apply_patch 工具，模型改用 `exec_command` 改文件，历史里只剩标准 `function_call`
- **`clean-custom-tool-calls.py` 清洗存量历史**：把 rollout 文件里的 `custom_tool_call` / `custom_tool_call_output` 改写成标准 `function_call` / `function_call_output`（`input` → `arguments`），让切换前录下的对话在第三方网关上也能续聊。待改文件会打包成单个 `tar.gz` 存到 `~/.codex/backups/`（受影响的 rollout 动辄数 GB，逐个复制会撑爆磁盘），改完必须接着跑 `remap-history-base.py` 重映射分叉偏移并失效 GUI 投影缓存
- **模型目录生成器跳过占位符密钥**：切换模板里留的是占位符，真密钥在钥匙串或 `~/.codex/config.toml` 里；生成器拿占位符去请求 `/models` 会得到 401，很难看出是哪一步错了
- **切换时清理无效的 `[model_providers.X.auth]` 段**：Codex 的 model provider 根本没有 `auth` 字段，这种「用命令取密钥」的写法会被静默忽略（不报错），结果等于没有凭据、平台直接返回 401。脚本现在按行删掉整段（段里的 `args = [...]` 数组行不会被漏掉，漏掉会写出非法 TOML 让 Codex 起不来）

## [1.6.1] - 2026-10-22

### Added

- **trim 7 天保护期 + 超限救援自动放行**：按文件名 `rollout-YYYY-MM-DD` 解析创建日期，不足 7 天的会话默认拒绝修剪（防止误裁近期活跃会话），`--force` 可放行；例外：回放负载（最新 compacted 窗口 + 尾部）估算超 1M token 窗口的年轻会话自动放行——不修剪根本跑不起来
- **schema 垫片「空 assistant 清理」**：codeproxy 转换时，完全空的 reasoning 条目（Codex 每轮都写，summary/content 皆空）和空 assistant 消息会生成 `content=null` 的裸 assistant 消息，Kimi 拒收报 `the message at position N with role 'assistant' must not be empty`，还会拖垮自动压缩（表现为「压缩已中断」）。垫片转发前丢弃这些空条目（summary 有文字的 reasoning 把文字搬进 content 保留）。垫片脚本收进仓库根目录，切换脚本 kimi 分支自动同步到 `~/.codex` 并重启垫片服务

## [1.6.0] - 2026-10-22

### Changed

- **kimi 默认模型升级为 `k3`（K3 1M，1,048,576 上下文窗口）**：Kimi 新上 K3 (1M) 型号后，旧默认 `k3-256k` 会让长会话续聊报 `Your request exceeded k3-256k model token limit: 262144`——界面选择器换模型不改变会话钉死的模型名，实际请求仍按 256K 发出。本次把 config 模板默认、脚本 `DEF_MODEL` 与历史改写映射目标全部改为 `k3`；切换时 1274+ 条已钉死 `k3-256k` 的会话会自动翻转，旧模型名（`k3-256k` / `kimi-*`）在改写通道里也会归一到 `k3`

## [1.5.3] - 2026-10-21

### Fixed

- **trim 覆盖 MCP 图片结果形态**：MCP 工具返回的图片是 `{type:'image', data: base64, mimeType}` 结构（图片在 `data` 字段而非 `image_url`），此前三版均不识别——实测一个 09-26 的会话因此残留 166 条共 10.2MB 图片结果。现在一并换 1x1 占位图（该会话回放负载从 12.1MB 降到 1.8MB）

## [1.5.2] - 2026-10-21

### Fixed

- **trim 保护区内也换 base64 图片**：最近工作区（默认尾部 2MB）此前完全不动，但 UI 快照工具产生的大截图（实测单张 2.3MB）落在保护区内时，请求体仍会超第三方网关 nginx 上限（kimi 模式续聊报 `413 Payload Too Large`）。现在保护区只保护文字，图片一律换 1x1 占位图（实测一个 21.4MB 的活跃工作会话剪到 1.2MB，kimi 模式恢复可续聊）

## [1.5.1] - 2026-10-21

### Fixed

- **trim 覆盖工具输出列表内嵌的 base64 截图**：UI 快照类工具会把截图以 `input_image`（data: URL）形式混在 `custom_tool_call_output` / `function_call_output` 的 `output` 列表与 MCP 结果 `content` 列表里，单张可达 1.6MB；v1.5.0 只处理消息 content 里的图片，遗漏了这类。现在一并换 1x1 占位图（实测一个 8.5MB 的活跃工作会话因此多剪掉 4.2MB）

## [1.5.0] - 2026-10-21

### Added

- **巨型会话智能修剪（`trim-rollout-output.py`）**：长期使用的会话续聊时报 `Your input exceeds the context window of this model`——App 把全量历史回放给模型，体积大头不是对话文字而是历年的工具输出（图像生成 base64、MCP 工具输出、命令回显、粘贴的 base64 图片、compacted 窗口记录里的 `guardian_history` 审查副本）。本工具只裁旧工具输出与粘贴大文件，user/assistant 消息文字一律不动；base64 图片换 1x1 占位 PNG 保持结构合法，超长文本保留开头 500 字符加占位说明，文件尾部 2MB 最近工作区不动（compacted 窗口记录除外），原文件自动备份 `.bak-trim-<时间戳>` 并失效相关线程（含分叉）投影缓存。实测 20.8MB 会话剪到 7.6MB，回放负载（最新窗口+尾部）从 ~2.7MB 降到 ~181KB。用法：`python3 trim-rollout-output.py <rollout.jsonl> [--keep-recent-mb 2] [--max-field-chars 4000] [--max-msg-chars 20000]`，运行后必须再跑 `remap-history-base.py` 修复分叉偏移

### Fixed

- **remap 兼容 `.jsonl.zst` 压缩分叉源**：App 0.163.x 对压缩源按**压缩后字节数**校验 lineage 偏移，而 `history_base.end_byte_offset` 记录的是解压后偏移——源一旦被维护任务压成 `.zst` 必然越界，症状为打开会话反复重新连接、标签页提示无法打开文件。现在候选源为 `.zst` 时先解压回 `.jsonl` 再计算偏移（原文件改名 `.zst.bak-lineage` 留档不再参与扫描），App 再次压缩则下次运行自动自愈；同时支持读取压缩源（`zstd -dc`，偏移按解压后字节计）与目标 ordinal 已被清理删除时的「下一个幸存行/文件末尾」回退

## [1.4.0] - 2026-09-18

### Added

- **默认模型预设**：切换后无需在界面重新选择模型——openai→`gpt-5.6-terra`、kimi→`k3-256k`、deepseek→`deepseek-v4-flash`，强度统一 medium。除 config 模板的新会话默认值外，切换脚本还会翻转 `state_5.sqlite` 的 `threads` 表中每个会话钉死的 `model` / `reasoning_effort`，否则打开旧会话仍用旧模型执行失败。可按需修改脚本里的 `DEF_MODEL` 与各 config 模板的 `model` 字段

## [1.3.0] - 2026-09-18

### Added

- **分叉会话 `history_base` 偏移重映射（`remap-history-base.py`）**：分叉会话文件头内嵌的 `history_base.end_byte_offset` 指向源 rollout 文件的字节位置；历史改写改变源文件长度后偏移越界，App 报 `invalid paginated history lineage: cutoff byte offset is past the source rollout`、对话无法打开（偏移在 jsonl 里而非数据库，失效投影缓存无法解决）。新脚本按 `end_ordinal_exclusive` 在源文件中重新定位同一行并把 cutoff 更新为当前字节偏移，支持多级分叉链，幂等可反复运行，修复后自动失效对应线程的投影缓存。每次切换自动调用，也可独立运行：`python3 remap-history-base.py ~/.codex`

### Changed

- **历史改写改为增量 + 流式**：模型名改写与第三方残留清理各记录独立的处理时间戳（`~/.codex/.switch-rewrite-marker-model` / `-reasoning`），只扫描上次处理后变动过的文件（归档会话处理一次即永久跳过）；全程逐行流式读写，不再把整个文件读进内存，无改动的文件只读不写。切换耗时不再随会话积累增长（实测 14 GB / 448 个文件、单文件最大 464 MB 的会话库）。升级后首次切换会做最后一次全量扫描建立基线

## [1.2.0] - 2026-09-11

### 新增

- **第三方模式的浏览器控制**：Codex 内嵌浏览器 / Computer Use 是 OpenAI 云端服务，第三方模式下必然报 `Codex auth token is unavailable`。新增两种基于 Kimi WebBridge 的桥接，让 Codex 在第三方模式下操作用户真实浏览器（自带登录态）：
  - `skills/webbridge-browser/SKILL.md`（推荐主路径）：教 Codex 用 `exec_command` curl 调用 WebBridge 守护进程，实测全链路通过
  - `webbridge-mcp.py`（备用）：MCP stdio 桥接服务。注意 Codex 0.153.4 将 `tool_search_always_defer_mcp_tools` 硬编码为 true，MCP 工具在 exec 模式下不可见，仅供 GUI 的 tool_suggest 发现

## [1.1.0] - 2026-09-10

### 修复

- **GUI 投影缓存失效（2026-09-09）**：Codex GUI 从 `thread_history_1.sqlite` 的投影（按字节偏移索引 rollout 文件）读取对话；脚本原地改写文件后偏移失效，GUI 会停在旧位置显示过期内容。现在脚本在改写/清理后自动失效受影响线程的投影，app 重启后从 jsonl 重建。（`4cae38c`）
- **官方模式续聊的四重校验（2026-09-08/09）**：第三方直连产生的历史项会依次触发官方 API 的四层校验——reasoning `content` 数组（`array_above_max_length`）、本地 Fernet `encrypted_content`（`invalid_encrypted_content`）、工具调用 `tool_` 前缀 id（`invalid_id_prefix`）、reasoning 的第三方 `rs_` id（`Item with id 'rs_...' not found`）。切到 openai 时脚本自动剥离 reasoning 的 content/encrypted_content/id 并把工具 id 前缀改为 `fc`，实测可正常续聊。（`15538dc`）
- **Kimi schema 校验（2026-09-08）**：新增 8788 端口 schema 垫片，修复 MCP 工具的 `$ref` + 兄弟 `type` 写法被 Kimi 校验器拒绝（`moonshot flavored json schema`）的问题，K3/K2.7 均实测 200。（`e6505f7`）

### 文档

- **双向续聊实测结论（2026-09-08）**：README 完整记录正/反向跨供应商续聊的实测结果、四层校验逐层症状与清理代价
- **故障排查表扩充（2026-09-09）**：新增「对话内容停在旧时间」「切换后对话从列表消失」两条症状，附 config 与 `state_5.sqlite` 标记一致性自查命令
- **CC Switch 定位定调（2026-09-07）**：实测确认 CC Switch（v3.19.2 / v3.20.1，含接管模式）点「启用」不会写入 `~/.codex/config.toml`，文档明确其仅作只读面板、真实切换必须走脚本
- 新增英文 README（`3285b92`）、徽章（`d45f61c`）、社交预览图（`fcf2b79`）

## [1.0.0] - 2026-09-08

首个公开发布版本。

### 新增

- **一键三向切换**：`codex-switch.sh openai|deepseek|kimi` + 三个 `.command` 双击入口，自动完成退出应用 → 写入配置 → 同步会话显示标记 → 改写历史模型名 → 重启应用
- **跨供应商续聊自动化**：
  - 模型名改写：解决续聊 pre-sampling compact 钉死会话元数据模型名导致的 `invalid_request_error`
  - 第三方历史残留清理：切回官方时自动处理 reasoning 残留字段
  - 所有改写/清理前自动留首次备份（`.bak-model` / `.bak-reasoning` / `.bak-switch`）
- **Kimi 本地路由**：codeproxy（8787）+ launchd 常驻服务
- **模型目录分家**：DeepSeek / Kimi 各用独立模型目录，GUI 模型选择器只显示当前供应商的模型，杜绝错选
- **CC Switch 可选集成**：已安装则自动同步其「使用中」标记（仅面板展示，不依赖）

[Unreleased]: https://github.com/aipmer/codex-switch/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/aipmer/codex-switch/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/aipmer/codex-switch/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/aipmer/codex-switch/releases/tag/v1.0.0
