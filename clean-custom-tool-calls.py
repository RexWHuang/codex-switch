#!/usr/bin/env python3
"""clean-custom-tool-calls.py [--codex-home DIR] [--dry-run] [--mode convert|drop]

把 rollout 历史里的 custom_tool_call / custom_tool_call_output 改写成
标准 function_call / function_call_output。

背景
----
Codex 里「自由文本型」工具（apply_patch、exec）在 Responses API 里的调用记录
是两种特殊的 input item 类型：

    {"type": "custom_tool_call",        "call_id": ..., "name": "apply_patch", "input": "<自由文本>"}
    {"type": "custom_tool_call_output", "call_id": ..., "output": "..."}

OpenAI 官方 API 认这两种类型；但很多第三方网关（Token鲨 校园平台背后的
litellm 代理）**只认标准 function 调用**，回放历史时直接返回 HTTP 400：

    litellm.BadRequestError: OpenAIException -
    {"error":{"message":"Unsupported input item type: custom_tool_call (parameter=input)"}}

于是「切到 Token鲨 后打开老会话就报错」，而新会话没事。

改写规则
--------
    custom_tool_call        -> function_call
        name       原样保留（apply_patch / exec）
        input      -> arguments（JSON 字符串 {"input": "<原文>"}）
        call_id    保留
    custom_tool_call_output -> function_call_output
        call_id / output 原样保留

--mode drop 则直接删掉这些条目（网关若校验「工具名必须在 tools 列表里」时用）。

实测（2026-11-06，Token鲨 https://llmv1.ahau.edu.cn/v1）
----------------------------------------------------
* input 里含 custom_tool_call        -> HTTP 400（原始报错）
* input 里含改写后的 function_call   -> HTTP 200，response.created 正常
  （工具名 apply_patch 不在 tools 列表里也不校验）
* 所以 --mode convert 即可，不必删条目。

注意
----
1. 改写会改变文件字节长度，分叉（fork）会话文件头部的 history_base.end_byte_offset
   会越界，App 报 "cutoff byte offset is past the source rollout"。
   **改完必须接着跑 remap-history-base.py**，它会重映射偏移并失效 GUI 投影缓存。
2. 备份：默认把「所有将被改动的文件」打进一个 tar.gz（~/.codex/backups/ 下），
   而不是逐文件复制 .bak——受影响的 rollout 动辄 2GB+，逐个复制会撑爆磁盘。
   --keep-file-bak 可额外留逐文件副本。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

ZSTD = shutil.which('zstd') or '/opt/homebrew/bin/zstd'
CUSTOM_TYPES = ('custom_tool_call', 'custom_tool_call_output')


def flatten_output(value):
    """custom_tool_call_output 的 output 可能是字符串，也可能是内容块数组
    （Codex 0.166 桌面端写的是 [{"type":"input_text","text":"..."}]）。
    function_call_output 的 output 是字符串，这里统一拍平。"""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for p in value:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict):
                parts.append(str(p.get('text') or p.get('content') or ''))
            else:
                parts.append(str(p))
        return ''.join(parts)
    if value is None:
        return ''
    return json.dumps(value, ensure_ascii=False)


def convert_item(payload):
    """把一条 custom_tool_call / custom_tool_call_output 转成标准 function 版本。
    返回 (新 payload, 是否改动)。"""
    t = payload.get('type')
    if t == 'custom_tool_call':
        text = payload.get('input')
        if not isinstance(text, str):
            text = '' if text is None else json.dumps(text, ensure_ascii=False)
        new = {
            'type': 'function_call',
            'name': payload.get('name') or 'apply_patch',
            'arguments': json.dumps({'input': text}, ensure_ascii=False),
            'call_id': payload.get('call_id'),
        }
        return new, True
    if t == 'custom_tool_call_output':
        new = {
            'type': 'function_call_output',
            'call_id': payload.get('call_id'),
            'output': flatten_output(payload.get('output')),
        }
        return new, True
    return payload, False


def rewrite_text(text, mode):
    """按行处理 rollout 文本，返回 (新文本, 改动的条目数, 删掉的行数)。

    注意：**必须按 '\\n' 切，不能用 str.splitlines()**。splitlines 会在
    \\x0b \\x0c \\x1c-\\x1e \\x85 \\u2028 \\u2029 这些字符处断行，
    而这些字符经常出现在工具输出里（终端分页符、Unicode 行分隔符），
    一旦断行，一条完整 JSON 记录会被劈成两半 → json.loads 失败 → 该条目被漏改。
    （2026-11-06 实测漏了 7 条 custom_tool_call_output。）
    """
    out, changed, dropped = [], 0, 0
    segments = text.split('\n')
    for seg in segments:
        stripped = seg.strip()
        if not stripped:
            out.append(seg)
            continue
        if 'custom_tool_call' not in stripped:
            out.append(seg)
            continue
        try:
            rec = json.loads(stripped)
        except Exception:
            out.append(seg)
            continue
        payload = rec.get('payload')
        if not isinstance(payload, dict) or payload.get('type') not in CUSTOM_TYPES:
            out.append(seg)
            continue
        if mode == 'drop':
            dropped += 1
            continue
        new_payload, ok = convert_item(payload)
        if not ok:
            out.append(seg)
            continue
        rec['payload'] = new_payload
        out.append(json.dumps(rec, ensure_ascii=False))
        changed += 1
    return '\n'.join(out), changed, dropped


def read_text(path):
    """读取 rollout 内容（.zst 先解压）。返回 (文本, 临时解压路径 或 None)。"""
    if path.endswith('.zst'):
        if not os.path.exists(ZSTD):
            return None, None
        fd, plain = tempfile.mkstemp(suffix='.jsonl')
        os.close(fd)
        with open(plain, 'wb') as out:
            rc = subprocess.run([ZSTD, '-dc', path], stdout=out).returncode
        if rc != 0 or os.path.getsize(plain) == 0:
            os.unlink(plain)
            return None, None
        with open(plain, 'r', encoding='utf-8') as fh:
            return fh.read(), plain
    with open(path, 'r', encoding='utf-8') as fh:
        return fh.read(), None


def write_text(path, text, plain_tmp):
    """写回 rollout（.zst 重新压缩）。"""
    if path.endswith('.zst'):
        packed = plain_tmp + '.zst'
        rc = subprocess.run([ZSTD, '-q', '-f', '-o', packed, plain_tmp]).returncode
        if rc != 0:
            return False
        shutil.copyfile(packed, path)
        return True
    tmp = path + '.tmp-precustom'
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write(text)
    os.replace(tmp, path)
    return True


def process_file(path, mode):
    """返回 (改动的条目数, 删除的行数)；无改动或读取失败返回 (0, 0)。"""
    text, plain_tmp = read_text(path)
    if text is None:
        return 0, 0
    try:
        new_text, changed, dropped = rewrite_text(text, mode)
    finally:
        if plain_tmp and os.path.exists(plain_tmp):
            os.unlink(plain_tmp)
    if not changed and not dropped:
        return 0, 0
    return changed, dropped, new_text


def make_backup(files, home):
    """把所有待改文件打进一个 tar.gz，返回备份路径。"""
    backup_dir = os.path.join(home, 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    ts = time.strftime('%Y%m%d-%H%M%S')
    out = os.path.join(backup_dir, 'rollouts-custom-tool-call-%s.tar.gz' % ts)
    with tarfile.open(out, 'w:gz') as tf:
        for p in files:
            tf.add(p, arcname=os.path.relpath(p, home))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--codex-home', default=os.path.expanduser('~/.codex'),
                    help='Codex 主目录（默认 ~/.codex）')
    ap.add_argument('--dry-run', action='store_true', help='只统计不落盘')
    ap.add_argument('--mode', choices=('convert', 'drop'), default='convert',
                    help='convert=改写成 function_call（默认）；drop=直接删除条目')
    ap.add_argument('--no-backup', action='store_true', help='不打包备份（不建议）')
    ap.add_argument('--keep-file-bak', action='store_true',
                    help='额外给每个改动的文件留 .bak-precustom 副本')
    args = ap.parse_args()

    files = []
    for root in ('sessions', 'archived_sessions'):
        rp = os.path.join(args.codex_home, root)
        if not os.path.isdir(rp):
            continue
        for dp, _, fs in os.walk(rp):
            for fn in fs:
                if fn.endswith('.jsonl') or fn.endswith('.jsonl.zst'):
                    files.append(os.path.join(dp, fn))

    # 先扫一遍，找出真正会被改动的文件（避免给无关文件也做备份）
    targets = []
    total_changed = total_dropped = 0
    for p in sorted(files):
        try:
            with open(p, 'rb') as fh:
                if b'custom_tool_call' not in fh.read():
                    continue
        except Exception:
            continue
        text, plain_tmp = read_text(p)
        if text is None:
            continue
        try:
            _, changed, dropped = rewrite_text(text, args.mode)
        finally:
            if plain_tmp and os.path.exists(plain_tmp):
                os.unlink(plain_tmp)
        if changed or dropped:
            targets.append(p)
            total_changed += changed
            total_dropped += dropped

    print(f'扫描 {len(files)} 个会话文件，{len(targets)} 个需要改写'
          f'（共 {total_changed} 条条目'
          + (f'、{total_dropped} 条待删除' if total_dropped else '') + '）')
    if not targets:
        return

    if args.dry_run:
        print('\n[试跑] 未落盘。')
        return

    backup_path = None
    if not args.no_backup:
        backup_path = make_backup(targets, args.codex_home)
        print(f'已备份 {len(targets)} 个文件 -> {backup_path}')

    done = 0
    for p in targets:
        text, plain_tmp = read_text(p)
        if text is None:
            print(f'⚠️  读取失败，跳过 {os.path.basename(p)}')
            continue
        new_text, changed, dropped = rewrite_text(text, args.mode)
        if plain_tmp:
            with open(plain_tmp, 'w', encoding='utf-8') as fh:
                fh.write(new_text)
        if args.keep_file_bak:
            shutil.copy2(p, p + '.bak-precustom')
        if write_text(p, new_text, plain_tmp):
            done += 1
        if plain_tmp and os.path.exists(plain_tmp):
            os.unlink(plain_tmp)

    print(f'完成：改写 {done} 个会话文件')
    print('⚠️  接着运行 remap-history-base.py 重映射分叉偏移并失效 GUI 投影缓存。')


if __name__ == '__main__':
    main()
