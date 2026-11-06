#!/usr/bin/env python3
"""诊断 Codex 供应商切换状态：定位「桌面端打不开会话」这类问题的根因。

用法：
    python3 check-provider.py [--home ~/.codex] [--dir <脚本目录>] [--live]

检查项（全部只读，不改任何数据）：
1. 当前 ~/.codex/config.toml 生效的 model_provider 与模型名
2. state_5.sqlite 里会话的 provider 分布，以及与当前配置不一致的会话数
   —— 这两者不一致就是 modelProviderMismatch（"This chat uses X, but your
   current provider is Y"）的根因，也是桌面端「继续聊天未成功」的最常见原因
3. 当前 provider 段的认证方式：env_key 变量在当前 shell / launchctl GUI 会话里是否可见
   —— GUI 应用由 launchd 启动，拿不到 shell 里 export 的变量，env_key 必然取不到
4. model_catalog_json 指向的模型目录是否存在、包含哪些模型
5. 桌面端日志里最近的 provider 相关报错

--live 会额外做一次真实的 API 连通性调用（消耗极少量 token）。
"""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys

OK, WARN, BAD, INFO = '  OK  ', ' WARN ', ' FAIL ', ' INFO '


def read_text(path):
    try:
        with open(path, encoding='utf-8') as fh:
            return fh.read()
    except OSError:
        return ''


def top_level_provider(text):
    m = re.search(r'^model_provider\s*=\s*"([^"\n]+)"', text, re.M)
    return m.group(1) if m else None


def top_level_value(text, key):
    m = re.search(r'^' + re.escape(key) + r'\s*=\s*"([^"\n]+)"', text, re.M)
    return m.group(1) if m else None


def provider_section(text, provider):
    m = re.search(r'^\[model_providers\.' + re.escape(provider) + r'\]\s*$(.*?)(?=^\[|\Z)',
                  text, re.M | re.S)
    return m.group(1) if m else None


def section_value(body, key):
    if not body:
        return None
    m = re.search(r'^' + re.escape(key) + r'\s*=\s*"([^"\n]+)"', body, re.M)
    return m.group(1) if m else None


def launchctl_env(name):
    try:
        out = subprocess.run(['launchctl', 'getenv', name], capture_output=True, text=True, timeout=5)
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ''


def main():
    parser = argparse.ArgumentParser(description='诊断 Codex 供应商切换状态')
    parser.add_argument('--home', default=os.environ.get('CODEX_HOME', os.path.expanduser('~/.codex')))
    parser.add_argument('--dir', default=os.path.dirname(os.path.abspath(__file__)))
    parser.add_argument('--live', action='store_true', help='额外做一次真实 API 调用')
    args = parser.parse_args()
    home = os.path.expanduser(args.home)

    print('== Codex 供应商诊断 ==')
    print(f'CODEX_HOME: {home}\n')

    config_path = os.path.join(home, 'config.toml')
    text = read_text(config_path)
    if not text:
        print(f'{BAD} 读不到 {config_path}')
        return 1

    provider = top_level_provider(text) or 'openai'
    model = top_level_value(text, 'model') or '(未设置)'
    print(f'{INFO} 当前配置: model_provider = "{provider}", model = "{model}"')

    # ---- 会话 provider 与配置是否一致 ----
    db_path = os.path.join(home, 'state_5.sqlite')
    mismatched = 0
    total = 0
    if os.path.exists(db_path):
        try:
            con = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
            rows = con.execute('select model_provider, count(*) from threads group by model_provider').fetchall()
            con.close()
            total = sum(c for _, c in rows)
            print(f'{INFO} 会话库 provider 分布（共 {total} 条会话）:')
            for name, count in sorted(rows, key=lambda r: -r[1]):
                flag = '' if name == provider else '   <- 与当前配置不一致'
                print(f'       {name:12} {count:5}{flag}')
            mismatched = sum(c for n, c in rows if n != provider)
        except sqlite3.Error as exc:
            print(f'{WARN} 读取 state_5.sqlite 失败: {exc}')
    else:
        print(f'{WARN} 找不到 {db_path}')

    if mismatched:
        print(f'{BAD} {mismatched} 条会话的 provider 与当前配置 "{provider}" 不一致，'
              f'这些会话在桌面端会报 modelProviderMismatch（"继续聊天未成功"）。')
        print(f'       修复：运行 codex-switch.sh <目标模式> 重新同步标记。')
    elif total:
        print(f'{OK} 全部 {total} 条会话的 provider 与当前配置一致。')

    # ---- 认证方式是否在 GUI 环境可用 ----
    body = provider_section(text, provider)
    if body is None and provider != 'openai':
        print(f'{BAD} 配置里没有 [model_providers.{provider}] 段，Codex 无法构造该 provider。')
    else:
        env_key = section_value(body, 'env_key')
        token = section_value(body, 'experimental_bearer_token')
        if env_key:
            in_shell = os.environ.get(env_key, '')
            in_gui = launchctl_env(env_key)
            print(f'{INFO} 认证方式: env_key = {env_key}')
            if in_shell:
                print(f'{OK} 该变量在当前 shell 可见（长度 {len(in_shell)}）')
            else:
                print(f'{WARN} 该变量在当前 shell 不可见')
            if in_gui:
                print(f'{OK} 该变量在 launchctl(GUI) 会话可见，桌面端可以取到')
            else:
                print(f'{BAD} 该变量在 launchctl(GUI) 会话不可见 —— 桌面端由 launchd 启动，'
                      f'拿不到 shell 里 export 的变量，必然报 "Missing environment variable"。')
                print(f'       修复二选一：')
                print(f'         a) 改用内联密钥：[{provider}] 段加 experimental_bearer_token = "sk-..."')
                print(f'         b) 写入 GUI 会话环境：launchctl setenv {env_key} <值>（重启 App 生效）')
        elif token:
            print(f'{OK} 认证方式: 内联 experimental_bearer_token（GUI 环境不受限）')
        elif provider == 'openai':
            print(f'{OK} 认证方式: 官方登录态（读 ~/.codex/auth.json）')

    # ---- 模型目录 ----
    catalog = top_level_value(text, 'model_catalog_json')
    if catalog:
        path = os.path.expanduser(catalog)
        if os.path.exists(path):
            try:
                data = json.loads(read_text(path))
                slugs = [m.get('slug') for m in data.get('models', [])]
                print(f'{OK} 模型目录 {path}: {", ".join(s for s in slugs if s)}')
                bad = [m.get('slug') for m in data.get('models', [])
                       if m.get('tool_mode') == 'code_mode_only']
                if bad:
                    print(f'{BAD} 目录里 {bad} 带 tool_mode=code_mode_only，'
                          f'会让 Codex 发送自定义 exec 工具，第三方 Responses 端点会拒绝。')
                    print(f'       修复：重新生成 python3 gen-model-catalog.py ...')
                if model not in slugs:
                    print(f'{WARN} 当前模型 {model} 不在目录里，Codex 会退回 fallback metadata。')
            except (ValueError, OSError) as exc:
                print(f'{BAD} 模型目录 {path} 解析失败: {exc}')
        else:
            print(f'{BAD} 模型目录不存在: {path}')
            print(f'       修复：python3 gen-model-catalog.py --provider {provider} '
                  f'--config {config_path} --out {path}')
    else:
        print(f'{WARN} 未设置 model_catalog_json，模型选择器只有官方模型，'
              f'且目标模型会退回 fallback metadata。')

    # ---- 桌面端日志里的 provider 报错 ----
    # 日志按日期分层存放（<日志目录>/YYYY/MM/DD/*.log），要递归扫描，
    # 否则只看顶层目录会漏掉全部实际日志，误报「没有报错」。
    log_dir = os.path.expanduser('~/Library/Logs/com.openai.codex')
    hits = []
    scanned = 0
    if os.path.isdir(log_dir):
        for dirpath, _, names in os.walk(log_dir):
            for name in names:
                if not name.endswith('.log'):
                    continue
                path = os.path.join(dirpath, name)
                scanned += 1
                try:
                    with open(path, encoding='utf-8', errors='ignore') as fh:
                        for line in fh:
                            if 'modelProviderMismatch' in line or 'your current provider is' in line:
                                hits.append(line.strip())
                except OSError:
                    continue
    if hits:
        print(f'{WARN} 桌面端日志里有 {len(hits)} 条 provider 不匹配报错（已扫 {scanned} 个日志文件），最近一条：')
        print('       ' + hits[-1][:300])
    else:
        print(f'{OK} 桌面端日志里没有 provider 不匹配报错（已扫 {scanned} 个日志文件）')

    if args.live and provider != 'openai':
        body = provider_section(text, provider) or ''
        base = section_value(body, 'base_url')
        token = section_value(body, 'experimental_bearer_token')
        if base and token:
            print(f'\n== 实时连通性 ==')
            try:
                out = subprocess.run(
                    ['curl', '-s', '-o', '/dev/null', '-w', '%{http_code}',
                     base.rstrip('/') + '/models',
                     '-H', f'Authorization: Bearer {token}'],
                    capture_output=True, text=True, timeout=30)
                code = out.stdout.strip()
                verdict = OK if code == '200' else BAD
                print(f'{verdict} GET {base}/models -> HTTP {code}')
            except (OSError, subprocess.SubprocessError) as exc:
                print(f'{WARN} 连通性测试失败: {exc}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
