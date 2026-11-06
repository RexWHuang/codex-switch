#!/bin/bash
# 注意：切换请只用本入口，不要在 CC Switch 界面点「启用」（它不写实际配置，详见 README.md）
# Token鲨（校园 LLM 网关）模式：先做网关预检，网关不通会直接中止且不动原配置。
"$(cd "$(dirname "$0")" && pwd)/codex-switch.sh" token_shark
read -n 1 -s -r -p "按任意键关闭窗口..."
