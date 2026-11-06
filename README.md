# codex-switch

> 此 fork 在 Codex 0.166.0-alpha.6 上仍属实验状态：隔离 CLI 会话已实测 ChatGPT 订阅 → DeepSeek API → ChatGPT 和反方向切换，两者都能读取切换前的上下文；另以约 3.6 万字符的合成上下文，通过 app-server 实测两方压缩后继续切换。一份 91 条记录的旧会话副本离线切换至 DeepSeek 后，除模型元数据外的内容未变化；一份包含压缩父会话的真实分叉副本切换后能由 app-server 加载历史。原会话未改动。桌面界面、接近上下文上限的会话和其他真实历史尚未验证。切换会覆盖整个配置并改写历史；使用前请单独完整备份 `~/.codex`，不要把 API 密钥提交到公开仓库。
>
> DeepSeek 默认模型改为官方当前的 `deepseek-flash`；会话默认模型和推理强度读取对应配置模板。切换供应商会重新扫描全部历史，同一供应商重复执行仍按增量处理。上一次配置备份在 `config.toml.bak-switch`，再次切换会覆盖此备份。
>
> 切换会先将 `.jsonl.zst` 会话解压为 `.jsonl`，原压缩文件保留为 `.zst.bak-switch`，同时更新使用压缩路径的索引。需要安装 `zstd`，并预留解压后的磁盘空间；遇到同名压缩与未压缩会话时会在切换前停止，需先人工核对。

[English README](README.EN.md) | [更新日志](docs/Changelog.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-macOS-lightgrey.svg)]()
[![Shell](https://img.shields.io/badge/shell-bash-4EAA25.svg)]()
[![Codex](https://img.shields.io/badge/tested-Codex%200.153.4-blue.svg)]()


macOS 下 Codex（ChatGPT 桌面应用内置 CLI）的**多供应商一键切换**工具：在 OpenAI 官方（ChatGPT 会员）、DeepSeek、Kimi Code 之间秒切，**历史会话跨供应商无缝续聊**。

适用场景：GPT 会员额度用完/到期时用第三方模型顶班，额度恢复后切回官方——**双向老对话都能接着聊**，不用新开窗口重述上下文。

> 所有结论均为 2026-09 在 Codex 0.153.4 + CC Switch 3.20.1 上实测得出，非推测。

## 功能

- 一键切换：退出 Codex 应用 → 写入对应配置 → 同步会话显示标记 → 重启应用
- **Token鲨（校园 LLM 网关）模式**（2026-11-06 新增）：`./codex-switch.sh token_shark`。切换前先从 macOS 钥匙串取密钥，再做网关连通性预检与协议探测（`/responses` 还是 `/chat/completions`），预检不过就中止且**原配置一个字都不动**——网关不通比额度耗尽更糟。密钥只存在钥匙串里，不进仓库、不进模板；切换时注入到 `config.toml`（600 权限）
- **provider 标记通用化**（2026-11-06）：会话能否打开取决于 `state_5.sqlite` 的 `threads.model_provider`、rollout 里 `session_meta.model_provider` 与配置里的 `model_provider` 三者是否同名，任一不一致桌面端就报 `modelProviderMismatch`（"This chat uses custom, but your current provider is deepseek"），表现为「继续聊天未成功」。脚本原先把 provider 硬编码成 `custom`，改了配置里的名字就会全线失效；现在从配置自动读出实际名称，并把数据库与 rollout 里的标记一起刷成它
- **只读诊断命令** `./codex-switch.sh check`：打印当前 provider、会话标记分布、认证方式在 GUI 环境是否可用、模型目录状态、桌面端日志中的相关报错；加 `--live` 另做一次真实 API 连通性调用。不改动任何数据
- **跨供应商续聊自动化**（本项目核心价值，两个实测出来的坑都已内置处理）：
  1. **模型名改写**：codex 续聊时的 pre-sampling compact 钉死使用会话元数据里记录的模型名，与当前配置无关。跨供应商续聊会被对方 API 以 `invalid_request_error` 拒绝。脚本在每次切换时把历史会话 rollout 里的 `"model":"..."` 批量改写为目标供应商的模型名。
  2. **第三方历史残留清理**（仅切回官方时）：第三方直连 `/responses` 产生的历史项会撞上官方 API 四重校验——reasoning 的 `content` 数组（`array_above_max_length`）、本地 Fernet `encrypted_content`（`invalid_encrypted_content`）、reasoning 的 `rs_` id（`Item with id 'rs_...' not found`，官方 store=false 无记录）、工具调用 `tool_` 前缀 id（`invalid_id_prefix`，官方要求 `fc` 开头）。切到 openai 时脚本递归剥离 reasoning 的 content/encrypted_content/id 并把工具 id 前缀改为 `fc`，官方只收到 summary，可正常续聊。
  3. **GUI 投影缓存失效**：Codex GUI 从 `thread_history_1.sqlite` 的投影（按字节偏移索引 rollout 文件）读取对话；脚本原地改写文件后偏移失效，GUI 会停在旧位置显示过期内容。脚本在改写/清理后自动删除被影响线程的投影行，app 重启后从 jsonl 自动重建。
  4. **分叉会话 history_base 重映射**：分叉（fork）会话文件头内嵌 `history_base.end_byte_offset`，指向源 rollout 文件的字节位置；原地改写源文件后偏移越界，App 报 `invalid paginated history lineage: cutoff byte offset is past the source rollout`、对话无法打开（该偏移存在 jsonl 里而非数据库，失效缓存救不了）。`remap-history-base.py` 按 `end_ordinal_exclusive` 在源文件中重新定位同一行并把 cutoff 更新为当前字节偏移，支持多级分叉链，幂等可反复运行；每次切换自动调用，也可独立运行 `python3 remap-history-base.py ~/.codex` 修复存量。2026-10-21 起兼容 `.jsonl.zst` 压缩源：App 0.163.x 对压缩源按**压缩后字节数**校验偏移，源一旦被维护任务压成 `.zst` 必然越界（症状：打开会话反复重新连接、标签页提示无法打开文件），脚本会先把压缩源解压回 `.jsonl` 再计算偏移（原文件留 `.zst.bak-lineage` 备份），App 再次压缩则下次运行自动自愈。
- **增量 + 流式改写**（2026-09-18）：两趟改写各记录独立的处理时间戳，只扫上次处理后变动过的文件（归档会话处理一次即永久跳过）；全程逐行流式读写，不再把整个文件读进内存。切换耗时不再随会话积累增长（实测会话库 14 GB / 448 个文件、单文件最大 464 MB 的场景）。升级后首次切换会做最后一次全量扫描建立基线。
- **默认模型预设**（2026-09-18）：切换后无需在界面重新选择模型——openai→`gpt-5.6-terra`、kimi→`k3`（K3 1M，2026-10-22 起；旧名 `k3-256k` 会被自动改写）、deepseek→`deepseek-v4-flash`，强度统一 medium。除写入 config 的新会话默认值外，还会翻转 `threads` 表中每个会话钉死的 `model` / `reasoning_effort`，否则打开旧会话仍用旧模型执行失败（按需修改脚本里的 `DEF_MODEL` 与各 config 模板的 `model` 字段）。
- **巨型会话智能修剪**（2026-10-21，`trim-rollout-output.py`）：长期使用的会话续聊时报 `Your input exceeds the context window of this model`，因为 App 把全量历史回放给模型。体积大头不是对话文字而是历年的工具输出（图像生成 base64、MCP 工具输出、命令回显、粘贴的 base64 图片、compacted 窗口记录里的 `guardian_history` 审查副本——实测一个 2.7MB 窗口记录里回放正文仅 66KB、审查副本占 2.2MB）。本工具只裁旧工具输出与粘贴大文件，user/assistant 消息文字一律不动，模型仍记得「做过什么、结论是什么」；base64 图片换 1x1 占位图保持结构合法，超长文本保留开头 500 字符加占位说明，文件尾部 2MB 最近工作区文字不动（compacted 窗口记录除外；v1.5.2 起保护区内的 base64 图片也会换占位图——图片是纯体积，上下文由文字承载），原文件自动备份 `.bak-trim-<时间戳>` 并失效相关线程投影缓存。修剪改变字节偏移，**运行后必须再跑 `remap-history-base.py`**。实测 20.8MB 会话剪到 7.6MB，回放负载从 ~2.7MB 降到 ~181KB。用法：`python3 trim-rollout-output.py <rollout.jsonl> [--keep-recent-mb 2] [--max-field-chars 4000] [--force]`。**7 天保护期**（v1.6.1）：按文件名 `rollout-YYYY-MM-DD` 解析创建日期，不足 7 天的会话默认拒绝修剪（近期历史仍活跃，误裁代价高），确需修剪加 `--force`；**例外**——回放负载（最新 compacted 窗口 + 尾部）估算超 1M token 窗口的年轻会话属超限救援，自动放行（不剪根本跑不起来）
- 所有改写/清理前自动留首次备份（`.bak-model` / `.bak-reasoning` / `.bak-switch`）
- 如已安装 CC Switch，自动同步其「使用中」标记（仅作面板展示）

## 为什么不能只用 CC Switch

CC Switch 点「启用」**只改它自己数据库的标记，不会写入 Codex 实际配置**——在 v3.19.2（issue #6236）和 v3.20.1（接管模式和普通模式均实测）上都验证过，日志显示"已接管"但 `~/.codex/config.toml` 的 mtime 根本未变。CC Switch 适合当配置仓库和用量面板，真实切换必须用本脚本。

## 安装

```bash
git clone https://github.com/aipmer/codex-switch.git
cd codex-switch
chmod +x codex-switch.sh *.command codeproxy-kimi.sh
```

1. 将 `config-deepseek.toml` 复制到 `~/.codex/switch-config-deepseek.toml`，设置权限为 600，再把 `YOUR_DEEPSEEK_API_KEY` 替换为你的 key；切勿将密钥写进仓库文件。脚本优先读取 `~/.codex/switch-config-<模式>.toml`，不存在时才读取仓库模板；缺少 DeepSeek 密钥时会在退出应用或改写数据前停止。
   这些文件是**完整配置**而不是增量覆盖：要保留现有项目、插件和其他设置，请把它们合入每个使用的模式文件。可将现有 `~/.codex/config.toml` 复制为 `~/.codex/switch-config-openai.toml` 以保留官方模式配置。
2. 需要 Kimi 的话，先装本地路由（见下节）
3. 需要 Token鲨 的话，把密钥存进钥匙串（见「Token鲨（校园 LLM 网关）模式」一节）
4. 使用：`./codex-switch.sh openai|deepseek|kimi|token_shark`，或双击对应 `.command`

## Kimi 本地路由（codeproxy）

新版 Codex 只支持 Responses API 且强制携带 `tool_search` 工具，Kimi 的 `/responses` 端点不认识它，直连必报 `invalid_request_error: tool type "tool_search" is not supported`。官方解法即本地路由（<https://www.kimi.com/zh-hans/resources/codex-api>）：

1. 按 Kimi Code 官方文档安装 codeproxy：<https://www.kimi.com/code/docs/>
2. 编辑 `codeproxy-kimi.sh`，替换 `YOUR_KIMI_API_KEY`，复制到 `~/.codex/`（launchd 可能无权访问 Documents 等目录）
3. 编辑 `com.codexswitch.codeproxy-kimi.plist`，把两处 `REPLACE_WITH_HOME` 替换为你的 home 目录绝对路径，复制到 `~/Library/LaunchAgents/`
4. 加载：`launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.codexswitch.codeproxy-kimi.plist`
5. Codex 桌面版内置 MCP 工具的 schema 会被 Kimi 校验器拒绝（`moonshot flavored json schema` 报错），因此还需 schema 垫片：把 `schema-shim-kimi.py` 复制到 `~/.codex/`，编辑 `com.codexswitch.schema-shim-kimi.plist` 的两处 `REPLACE_WITH_*` 后同样放入 LaunchAgents 并 bootstrap。链路为 Codex → 垫片(8788) → codeproxy(8787) → Kimi；`config-kimi.toml` 的 `base_url` 指向 8788。v1.6.1 起垫片还会清理 input 里完全空的 reasoning / 空 assistant 条目（它们经 codeproxy 转换会生成 `content=null` 的裸 assistant 消息，被 Kimi 拒收：`the message at position N with role 'assistant' must not be empty`，并拖垮自动压缩）；切换脚本在 kimi 分支会自动同步新版垫片并重启服务

之后 `codex-switch.sh kimi` 会自动确保路由在运行。

## Token鲨（校园 LLM 网关）模式

Token鲨 平台的 OpenAI 兼容网关。**对外接入地址是 `https://llmv1.ahau.edu.cn/v1`**（443 端口，校园网放行，直接可用）——学校《开通通知》《大模型公共服务平台使用手册》《常用模型接入工具使用说明》三处一致写明「平台固定接口地址为 https://llmv1.ahau.edu.cn/v1」，调用用申请的 API Key 鉴权，模型 ID 即模型名称。

1. 接口地址写在 `config-token-shark.toml` 的 `base_url`。**不要用平台接口 `GET /api/me/api-base-url/` 返回的那个地址**：它给的是 `http://llm.ahau.edu.cn:4000/v1`，那是平台**后端内部**的网关地址，学生侧直连会被校园网准入策略丢包（TCP 握手无应答），照抄它必然连不通。
2. 密钥放钥匙串（不进仓库、不进模板文件）：

   ```bash
   security add-generic-password -U -a token-shark -s codex-token-shark -w "<你的 sk- 密钥>"
   ```

3. 切换：`./codex-switch.sh token_shark`（或双击 `切换到Token鲨.command`）。脚本依次做四件事，任何一步不通过就中止且不动原配置：

   | 步骤 | 失败时的表现 |
   | --- | --- |
   | 从钥匙串取 `codex-token-shark`（取不到时回退到配置里内联的真实密钥） | `钥匙串里没有 codex-token-shark`，退出码 2 |
   | 预检 `GET {base_url}/models`（要求 HTTP 200 **且**响应体是 JSON） | `网关预检未通过（HTTP 000）`，退出码 3 |
   | 探测 `/responses` 与 `/chat/completions`，据此决定 `wire_api` | 两者都不存在则退出码 4 |
   | 注入密钥与模型目录，把会话 provider 标记刷成 `token_shark` | — |

   预检通过后，密钥会被写进 `~/.codex/config.toml`（权限 600），模型目录 `~/.codex/token-shark-models.json` 存在时自动写入 `model_catalog_json`。

**两个必须知道的坑**

- **Codex 的 model provider 没有 `auth` 字段**。`[model_providers.token_shark.auth]` 这种「用命令取密钥」的写法会被**静默忽略**（不报错），结果等于没有凭据，平台直接返回 401。脚本切换时会自动删掉这类段。认证只能用 `experimental_bearer_token = "sk-..."`（内联，桌面端 launchd 环境拿不到 shell 变量，`env_key` 在桌面端不可用）。
- **别把内部网关地址当对外入口**。`llm.ahau.edu.cn:4000` 只在平台内网可达：校园网 ACL 会把它的 SYN 直接丢包，在 80/81/8080 上则返回一张带 `Client IP` 的「Server Unavailable」拦截页，看着像服务挂了，其实是网络策略。**对策不是找管理员开端口，而是换域名**——用官方的 `https://llmv1.ahau.edu.cn/v1`。

  预检通过后脚本会自动选 `wire_api`：网关有 `/responses` 就用 `responses`（Codex 0.166+ 的原生协议），只有 `/chat/completions` 就退到 `chat`。

  地址临时换一处（比如平台又改了域名），不必改仓库模板，用环境变量覆盖即可：

  ```bash
  CODEX_SWITCH_TOKEN_SHARK_BASE_URL=https://llmv1.ahau.edu.cn/v1 ./codex-switch.sh token_shark
  ```

## 第三方模式的浏览器控制（可选）

Codex 的内嵌浏览器 / Computer Use 是 OpenAI 云端服务，第三方模式下必然报 `Codex auth token is unavailable`，无法使用。如果你安装了 [Kimi WebBridge](https://www.kimi.com/zh-cn/features/webbridge)（Kimi 官方浏览器插件，本地守护进程在 127.0.0.1:10086），本仓库提供两种桥接方式让 Codex 在第三方模式下也能操作用户真实浏览器（自带登录态）：

1. **Codex Skill（推荐主路径）**：把 `skills/webbridge-browser/SKILL.md` 复制到 `~/.codex/skills/webbridge-browser/`。Codex 会学会用 `exec_command` curl 调用 WebBridge（navigate / snapshot / click / fill / evaluate）。注意 `codex exec` 默认沙箱禁网，需 `-s danger-full-access` 或在 GUI 全权限配置下使用。
2. **MCP 桥接（备用）**：在 `config-kimi.toml` / `config-deepseek.toml` 末尾追加（路径按实际调整）：
   ```toml
   [mcp_servers.webbridge]
   command = "/usr/bin/python3"
   args = ["/path/to/webbridge-mcp.py"]
   startup_timeout_sec = 20
   tool_timeout_sec = 180
   ```
   注意：Codex 0.153.4 的 `tool_search_always_defer_mcp_tools` 已被官方标记 removed 并硬编码为 true，MCP 工具默认藏在 tool_search 后面，exec 模式下模型不可见——GUI 里可靠 tool_suggest 自动发现，所以主路径仍推荐 skill。

## 模型目录分家（可选但推荐）

若两套第三方配置共用一份模型目录（`model_catalog_json`），Codex 界面的模型选择器会把两家的模型全列出来——在 DeepSeek 模式选到 Kimi 模型会请求被拒。建议拆成两份各指各的：DeepSeek 模式只显示 `deepseek-flash / deepseek-v4-pro`，Kimi 模式只显示 `kimi-for-coding / k3` 等。

目录文件可用 `gen-model-catalog.py` 自动生成——它从供应商的 `/models` 接口取真实清单与上下文长度，
再套用 Codex 自带目录的字段模板：

```bash
python3 gen-model-catalog.py --provider custom \
    --config ~/.codex/switch-config-deepseek.toml \
    --out ~/.codex/model-catalog-deepseek.json
```

生成后在对应配置里加 `model_catalog_json = "/绝对路径/model-catalog-deepseek.json"`。

不配目录也能跑，但目标模型会退回 fallback metadata（控制台报 `Model metadata for \`deepseek-flash\` not found`），
界面的模型选择器里也只有官方模型。

> 两个已踩过的坑（脚本已规避）：目录里的 `tool_mode = "code_mode_only"` 会让 Codex 改用自定义
> `exec` 工具，第三方 Responses 端点直接拒绝（`Unsupported custom tool: 'exec'`）；`web_search_tool_type`
> 不能为 null，否则目录解析直接失败。

## 注意事项

- **切换会整体覆盖 `~/.codex/config.toml`**：请把 projects 信任列表、插件、notify 等个性化配置合并进本仓库的四个模板，否则切换后丢失
- 改写以切换时刻为准，切换后新产生的会话天然是当前模型名，无需处理
- 官方↔第三方来回切换时模型名会被来回改写（如 gpt-5.6-luna → deepseek-v4-flash → gpt-5.6-sol），归属精度有损失但不影响功能，原始值在 `.bak-model` 里
- 剥离 reasoning 后这些会话的 reasoning 正文不可恢复（`.bak-reasoning` 有原始文件）；再切回第三方时这些 reasoning 项只剩空 summary，DeepSeek/Kimi 侧对此容错，实测不受影响
- 会话已归档时需先 `codex unarchive <会话ID>` 再续聊

## 故障排查

先跑 `./codex-switch.sh check` 做只读诊断——它会打印当前生效的 provider、会话标记分布、
认证方式是否在 GUI 环境可用、模型目录状态，以及桌面端日志里的相关报错，不改动任何数据。
加 `--live` 会额外做一次真实 API 连通性调用。

| 症状 | 检查 |
| --- | --- |
| 桌面端「继续聊天未成功」，提示 `This chat uses custom, but your current provider is deepseek`（`modelProviderMismatch`） | 会话标记与配置里的 provider 名称不一致。app-server 打开会话时会把 `state_5.sqlite` 的 `threads.model_provider` 和 rollout 里 `session_meta.model_provider` 同当前配置的 `model_provider` 三者比对，任一不一致就拒绝打开。常见于改了配置里的 `model_provider` 名字却没重跑切换。跑 `./codex-switch.sh check` 看分布，重跑 `./codex-switch.sh <模式>` 同步（新版脚本会把数据库与 rollout 里的标记一起刷成配置里的名字） |
| 桌面端报 `Missing environment variable: DEEPSEEK_API_KEY`，但 CLI 正常 | 用了 `env_key` 认证。桌面端由 launchd 启动，拿不到 shell 里 `export` 的变量，只有 CLI 能读到。改用内联 `experimental_bearer_token = "sk-..."`（脚本已对 config.toml 设 600 权限），或 `launchctl setenv DEEPSEEK_API_KEY <值>` 后重启 App |
| 界面选不到 DeepSeek 模型，或报 `Model metadata for \`deepseek-flash\` not found` | 没配模型目录，目标模型退回 fallback metadata。用 `python3 gen-model-catalog.py --provider custom --config ~/.codex/switch-config-deepseek.toml --out ~/.codex/model-catalog-deepseek.json` 生成，并在配置里加 `model_catalog_json` 指向它 |
| 第三方模式报 `Unsupported custom tool: 'exec'. Only 'apply_patch' is supported.` | 模型目录里带 `tool_mode = "code_mode_only"`，会让 Codex 改用自定义 exec 工具，第三方 Responses 端点不接受。`gen-model-catalog.py` 生成的目录已剔除该字段 |
| 恢复对话失败：`cutoff byte offset is past the source rollout`；或打开会话反复重新连接、提示无法打开文件 | 分叉会话文件头内嵌的 `history_base` 字节偏移越界：历史改写改变了源文件长度，或 App 0.163.x 把源文件压成 `.jsonl.zst` 后按压缩后字节校验。运行 `python3 remap-history-base.py ~/.codex`（自动解压压缩源并重映射）后重启 App；新版脚本已在每次切换后自动处理 |
| 续聊报 `Your input exceeds the context window of this model` | 会话历史全量回放超模型窗口，运行 `python3 trim-rollout-output.py <该会话的 rollout.jsonl>` 修剪旧工具输出（对话文字不动、自动备份），再跑 `python3 remap-history-base.py ~/.codex` 修复分叉偏移，重启 App |
| kimi 模式报 Connection refused | `launchctl print gui/$(id -u)/com.codexswitch.codeproxy-kimi` 看状态；`tail ~/.codex/codeproxy-kimi.log` 看日志；`launchctl kickstart -k gui/$(id -u)/com.codexswitch.codeproxy-kimi` 重启 |
| kimi 模式报 401 | Key 过期/失效，去 Kimi Code 控制台重建，更新 `~/.codex/codeproxy-kimi.sh` 里的 `--apikey` 后 kickstart 重启 |
| kimi 模式报 `not a valid moonshot flavored json schema` | 配置绕过了垫片（8788）直连了 codeproxy（8787），检查 `base_url` |
| kimi 模式报 `the message at position N with role 'assistant' must not be empty`（常伴「压缩已中断」） | 历史里有完全空的 reasoning/assistant 条目，codeproxy 转换出裸 assistant 被 Kimi 拒收。v1.6.1 起垫片自动清理；还在报说明垫片是旧版，重跑切换脚本同步新版垫片即可（会话文件本身不用动） |
| kimi 模式报 tool_search 不支持 | 配置走了直连而非路由，确认 `~/.codex/config.toml` 的 `base_url = "http://127.0.0.1:8787/v1"` |
| token_shark 模式报 `网关预检未通过（HTTP 000）` | `base_url` 填错了，多半填成了平台内部网关 `http://llm.ahau.edu.cn:4000/v1`（校园网 ACL 会丢包）。对外入口是 `https://llmv1.ahau.edu.cn/v1`，改对即可；排查用 `curl -sS https://llmv1.ahau.edu.cn/v1/models -H "Authorization: Bearer <sk- 密钥>"`，应返回模型列表 JSON |
| token_shark 模式报 401 | 密钥没注入或已失效。确认钥匙串里有 `codex-token-shark`（`security find-generic-password -a token-shark -s codex-token-shark -w`），并检查 `~/.codex/config.toml` 的 `[model_providers.token_shark]` 段里有 `experimental_bearer_token` |
| token_shark 模式报 401 但配置里明明有密钥 | 配置里写了 `[model_providers.token_shark.auth]` 这种「命令式取密钥」的段——Codex 没有这个字段，会被静默忽略，等于没有凭据。删掉该段，改用 `experimental_bearer_token` |
| 第三方模式下浏览器控制报 `Codex auth token is unavailable` | 内嵌浏览器/Computer Use 是 OpenAI 云端服务，第三方模式必然不可用。用「第三方模式的浏览器控制」一节的 WebBridge 桥接方案 |
| 切换后历史会话不见了 | 会话标记没同步，重跑一次切换脚本即可（脚本会翻转 `state_5.sqlite` 的 provider 标记） |
| 切换后对话内容停在旧时间、最新消息不显示 | GUI 投影缓存（`thread_history_1.sqlite`）按字节偏移索引 rollout 文件，脚本改写后偏移失效——脚本已自动失效受影响线程的投影；旧版本脚本可手动删除该库中对应 thread_id 的投影行（或整表清空），重启 app 自动重建，数据在 jsonl 里不会丢 |
| 官方模式续聊旧对话报 `array_above_max_length` / `invalid_encrypted_content` / `invalid_id_prefix` / `Item with id 'rs_...' not found` | 第三方历史残留（reasoning content/encrypted_content/rs_ id、工具调用 tool_ 前缀 id），切到 openai 时脚本会自动清理；仍遇到说明该会话是切换后新建的，重跑一次切换脚本 |
| 第三方模式续聊官方会话报 `passed gpt-5.x` | 会话元数据模型名未改写，重跑一次切换脚本 |
| 界面选到了别家模型 | 目录未分家，见「模型目录分家」一节 |
| 验证路由是否正常 | `curl -s http://127.0.0.1:8787/v1/responses -H "Content-Type: application/json" -d '{"model":"kimi-for-coding","input":"hi","stream":false}'` |

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `CODEX_HOME` | `~/.codex` | Codex 配置目录 |
| `CODEX_SWITCH_APP` | `ChatGPT` | Codex 桌面应用名 |
| `CODEX_SWITCH_CLI` | `/Applications/ChatGPT.app/Contents/Resources/codex` | CLI 路径（用于打印版本） |
| `CODEX_SWITCH_LAUNCHD_LABEL` | `com.codexswitch.codeproxy-kimi` | Kimi 路由的 launchd Label |
| `CODEX_SWITCH_TOKEN_SHARK_BASE_URL` | 取配置里的 `base_url` | 覆盖 Token鲨 网关地址（平台改用 443 反代后不必改仓库模板，例如 `https://llm.ahau.edu.cn/v1`） |

## 文件清单

- `codex-switch.sh` — 切换主脚本（`openai|deepseek|kimi|token_shark` 切换，`check` 只读诊断）
- `check-provider.py` — 供应商状态诊断（切换时自动可用，也可独立运行）
- `gen-model-catalog.py` — 生成第三方供应商的 Codex 模型目录
- `remap-history-base.py` — 分叉会话 history_base 偏移重映射（切换时自动调用，可独立运行）
- `trim-rollout-output.py` — 巨型会话智能修剪：裁旧工具输出/粘贴大图，保对话文字（需配合 remap 使用）
- `config-openai.toml` / `config-deepseek.toml` / `config-kimi.toml` / `config-token-shark.toml` — 四套配置模板
- `切换到*.command` — 双击入口
- `codeproxy-kimi.sh` + `com.codexswitch.codeproxy-kimi.plist` — Kimi 本地路由模板
- `schema-shim-kimi.py` + `com.codexswitch.schema-shim-kimi.plist` — Kimi schema 修正垫片模板（修 $ref 兄弟字段；v1.6.1 起还会清理会产生「空 assistant」的空 reasoning/空 assistant 条目）
- `webbridge-mcp.py` + `skills/webbridge-browser/SKILL.md` — 第三方模式的浏览器控制桥接（需 Kimi WebBridge）

## License

MIT
