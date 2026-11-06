#!/usr/bin/env python3
"""生成 Codex 模型目录（model_catalog_json），把某家第三方供应商的模型单独列出。

背景：Codex 自带的模型目录只有官方模型，第三方模式下界面选到别家模型会被 API 拒绝，
且缺失模型元数据时会退回 fallback metadata（warning: Model metadata for `x` not found）。

用法：
    python3 gen-model-catalog.py --provider deepseek \
        --config ~/.codex/switch-config-deepseek.toml \
        --out ~/.codex/model-catalog-deepseek.json

流程：
1. 用 `codex debug models` 取 Codex 自带目录，挑一条条目当模板（保证字段齐全）。
2. 从供应商 /models 接口取真实模型清单、上下文长度、推理档位。
3. 合并成 Codex 可读的 {"models": [...]}。

两个坑（实测确认）：
- 必须删掉模板里的 tool_mode=code_mode_only：它会让 Codex 改用自定义 `exec` 工具，
  而 DeepSeek 的 Responses 端点只接受 apply_patch，请求会被直接拒绝
  （Unsupported custom tool: 'exec'. Only 'apply_patch' is supported.）。
- web_search_tool_type 不能为 null（解析报 invalid type: null, expected string or map），
  第三方供应商没有内置联网搜索，填 "text" 占位、由 config 的 web_search = "disabled" 兜底。
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request


def read_api_key(config_path, provider):
    """从切换配置里取该 provider 的密钥。

    优先用目标 provider 段里的 experimental_bearer_token（内联，GUI 环境也能用）；
    其次用该段的 env_key 对应的环境变量；最后退而求其次，在配置里任意一个
    [model_providers.*] 段找内联 token——第三方配置常把 key 放在另一个 provider 段里。
    """
    if not config_path or not os.path.exists(config_path):
        return os.environ.get(f'{provider.upper()}_API_KEY', '')
    text = open(config_path, encoding='utf-8').read()
    blocks = re.findall(
        r'^\[model_providers\.([^\]]+)\]\s*$(.*?)(?=^\[|\Z)', text, re.M | re.S)
    for name, body in blocks:
        if name != provider:
            continue
        m = re.search(r'^experimental_bearer_token\s*=\s*"([^"]+)"', body, re.M)
        if m:
            return m.group(1)
        m = re.search(r'^env_key\s*=\s*"([^"]+)"', body, re.M)
        if m:
            value = os.environ.get(m.group(1), '')
            if value:
                return value
            break  # 环境变量没设，交给下面的兜底逻辑
    for name, body in blocks:
        m = re.search(r'^experimental_bearer_token\s*=\s*"([^"\n]+)"', body, re.M)
        if m and 'YOUR_' not in m.group(1):
            return m.group(1)
    return os.environ.get(f'{provider.upper()}_API_KEY', '')


def codex_template():
    """取 Codex 自带模型目录，用第一条做模板（字段齐全，避免手写漏字段）。"""
    env = dict(os.environ)
    tmp = os.path.join(env.get('TMPDIR', '/tmp'), 'codex-catalog-template')
    os.makedirs(tmp, exist_ok=True)
    env['CODEX_HOME'] = tmp  # 绕开本机 config 里的 model_catalog_json，取原始内置目录
    cli = os.environ.get('CODEX_SWITCH_CLI',
                         '/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex')
    if not os.path.exists(cli):
        cli = '/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex'
    out = subprocess.run([cli, 'debug', 'models'], capture_output=True, text=True, env=env)
    if out.returncode != 0:
        raise SystemExit('codex debug models 执行失败：' + out.stderr.strip())
    models = json.loads(out.stdout)['models']
    if not models:
        raise SystemExit('codex debug models 未返回任何模型')
    return models[0]


def fetch_models(base_url, api_key, timeout=30):
    url = base_url.rstrip('/') + '/models'
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {api_key}'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.load(resp)
    return payload.get('data', [])


EFFORT_DESCRIPTIONS = {
    'low': 'Fast responses with lighter reasoning',
    'medium': 'Balances speed and reasoning depth for everyday tasks',
    'high': 'Greater reasoning depth for complex tasks',
    'xhigh': 'Maximum reasoning depth for complex tasks',
    'max': 'Maximum reasoning depth for the hardest tasks',
    'ultra': 'Maximum reasoning with automatic task delegation',
}


def build_entry(template, spec, default_effort='high'):
    entry = json.loads(json.dumps(template))
    entry['slug'] = spec['id']
    entry['display_name'] = spec.get('name') or spec['id']
    entry['description'] = spec.get('description') or f"{entry['display_name']} via third-party API"
    effort = spec.get('effort') or {}
    levels = effort.get('supported_levels') or [default_effort]
    entry['default_reasoning_level'] = effort.get('default_level') or default_effort
    entry['supported_reasoning_levels'] = [
        {'effort': lv, 'description': EFFORT_DESCRIPTIONS.get(lv, lv)} for lv in levels
    ]
    context = spec.get('context_window')
    if context:
        entry['context_window'] = context
        entry['max_context_window'] = context
    modalities = spec.get('input_modalities')
    if modalities:
        entry['input_modalities'] = modalities
    entry['supports_search_tool'] = False
    entry['web_search_tool_type'] = 'text'   # 不能为 null，否则解析失败
    entry['supports_reasoning_effort_updates'] = False
    entry['use_responses_lite'] = False
    entry['service_tiers'] = []
    entry['additional_speed_tiers'] = []
    entry.pop('default_service_tier', None)
    entry['availability_nux'] = None
    entry['upgrade'] = None
    entry['priority'] = 1
    # code_mode_only 会让 Codex 发送自定义 exec 工具，第三方 Responses 端点不接受
    entry.pop('tool_mode', None)
    return entry


def main():
    parser = argparse.ArgumentParser(description='生成 Codex 第三方供应商模型目录')
    parser.add_argument('--provider', required=True, help='provider 名（对应配置里的 [model_providers.<name>]）')
    parser.add_argument('--config', help='切换配置文件，从中读取 base_url 与密钥')
    parser.add_argument('--base-url', help='API base_url（覆盖配置文件里的值）')
    parser.add_argument('--out', required=True, help='输出的目录 JSON 路径')
    parser.add_argument('--api-key', help='API key（默认从配置或环境变量读取）')
    args = parser.parse_args()

    text = ''
    if args.config and os.path.exists(args.config):
        text = open(args.config, encoding='utf-8').read()
    block = re.search(
        r'^\[model_providers\.' + re.escape(args.provider) + r'\]\s*$(.*?)(?=^\[|\Z)',
        text, re.M | re.S)
    if not block and not args.base_url:
        raise SystemExit(f'配置里找不到 [model_providers.{args.provider}]，请用 --base-url 指定')
    base_url = args.base_url
    if not base_url and block:
        m = re.search(r'^base_url\s*=\s*"([^"]+)"', block.group(1), re.M)
        base_url = m.group(1) if m else None
    if not base_url:
        raise SystemExit('未找到 base_url')

    api_key = args.api_key or read_api_key(args.config, args.provider)
    if not api_key:
        raise SystemExit('未找到 API key，请用 --api-key 指定')

    template = codex_template()
    specs = fetch_models(base_url, api_key)
    if not specs:
        raise SystemExit(f'{base_url}/models 未返回模型清单')

    catalog = {'models': [build_entry(template, s) for s in specs]}
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or '.', exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as fh:
        json.dump(catalog, fh, ensure_ascii=False, indent=2)
    os.chmod(args.out, 0o600)
    slugs = [m['slug'] for m in catalog['models']]
    print(f'已生成 {args.out}（{len(slugs)} 个模型）：{", ".join(slugs)}')


if __name__ == '__main__':
    main()
