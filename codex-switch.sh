#!/bin/bash
# Codex 供应商一键切换：./codex-switch.sh openai|deepseek|kimi|token_shark
# 自动完成：退出 Codex 应用 → 替换配置 → 同步会话显示标记 →
#           改写历史会话模型名 → （仅 openai）清理第三方 reasoning 残留 → 重启应用
#
# token_shark（校园 LLM 网关）模式额外做两步，且都发生在改动任何数据之前：
#   1) 从 macOS 钥匙串取密钥（不落仓库、不写进模板）
#   2) 网关连通性预检 + 协议探测（Responses 还是 Chat Completions）
#      预检不过就中止，原配置保持不变 —— 网关不通比额度耗尽更糟。
#
# 注意：如果你同时装了 CC Switch，不要在它的界面里点「启用」来切换——
# 它只改自己的数据库标记，不会写入 ~/.codex/config.toml（v3.19.2 与 v3.20.1
# 均已实测确认，接管模式同样不写）。真实切换只能跑本脚本或双击对应的 .command。
set -e
MODE="$1"
DIR="$(cd "$(dirname "$0")" && pwd)"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"

# Kimi 本地路由的 launchd 服务名（安装 codeproxy 时使用的 Label，
# 见 README「Kimi 本地路由」一节；不用 Kimi 可忽略）
LAUNCHD_LABEL="${CODEX_SWITCH_LAUNCHD_LABEL:-com.codexswitch.codeproxy-kimi}"
SHIM_LABEL="${CODEX_SWITCH_SHIM_LABEL:-com.codexswitch.schema-shim-kimi}"

# Codex 应用与 CLI 路径（默认 ChatGPT 桌面应用内置的 codex）
CODEX_APP="${CODEX_SWITCH_APP:-ChatGPT}"
CODEX_CLI="${CODEX_SWITCH_CLI:-/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex}"

if [ "$MODE" != "openai" ] && [ "$MODE" != "deepseek" ] && [ "$MODE" != "kimi" ] && [ "$MODE" != "token_shark" ] && [ "$MODE" != "check" ]; then
  echo "用法: $0 openai|deepseek|kimi|token_shark|check"; exit 1
fi

# 配置文件名用连字符，provider 名用下划线：两者不必同名
case "$MODE" in
  token_shark) MODE_TAG="token-shark";;
  *) MODE_TAG="$MODE";;
esac

CONFIG="$CODEX_HOME/switch-config-$MODE_TAG.toml"
[ -f "$CONFIG" ] || CONFIG="$DIR/config-$MODE_TAG.toml"
if [ "$MODE" = "check" ]; then
  # 只读诊断：定位「桌面端打不开会话」等问题的根因，不改动任何数据
  shift
  exec python3 "$DIR/check-provider.py" --home "$CODEX_HOME" --dir "$DIR" "$@"
fi
if [ "$MODE" = "deepseek" ] && grep -q 'YOUR_DEEPSEEK_API_KEY' "$CONFIG"; then
  echo "DeepSeek 密钥尚未配置：请使用 $CODEX_HOME/switch-config-deepseek.toml" >&2
  exit 1
fi
DEF_MODEL=$(python3 - "$CONFIG" <<'PYEOF'
import re, sys
with open(sys.argv[1], encoding='utf-8') as f:
    match = re.search(r'^model\s*=\s*"([^"\n]+)"', f.read(), re.M)
if not match:
    raise SystemExit('配置中没有 model')
print(match.group(1))
PYEOF
)
DEF_EFFORT=$(python3 - "$CONFIG" <<'PYEOF'
import re, sys
with open(sys.argv[1], encoding='utf-8') as f:
    match = re.search(r'^model_reasoning_effort\s*=\s*"([^"\n]+)"', f.read(), re.M)
print(match.group(1) if match else 'medium')
PYEOF
)

# ---------- Token鲨：取密钥 + 网关预检 + 协议探测（必须早于任何数据改动）----------
RENDERED=""
cleanup() {
  if [ -n "$RENDERED" ] && [ -f "$RENDERED" ]; then
    python3 -c 'import os, sys; os.remove(sys.argv[1])' "$RENDERED" 2>/dev/null || true
  fi
  return 0
}
trap cleanup EXIT
if [ "$MODE" = "token_shark" ]; then
  TS_KEY="$(security find-generic-password -a token-shark -s codex-token-shark -w 2>/dev/null || true)"
  if [ -z "$TS_KEY" ]; then
    echo "❌ 钥匙串里没有 codex-token-shark，取不到 Token鲨 密钥；原配置保持不变。" >&2
    exit 2
  fi
  RENDERED="$(mktemp "${TMPDIR:-/tmp}/codex-switch-token-shark.XXXXXX")"
  chmod 600 "$RENDERED"
  if ! TS_WIRE="$(python3 - "$CONFIG" "$RENDERED" "$CODEX_HOME/token-shark-models.json" "$TS_KEY" <<'PYEOF'
import json, os, re, subprocess, sys, tempfile

config_path, out_path, catalog_path, key = sys.argv[1:5]


def fail(message):
    sys.stderr.write(message + '\n')
    raise SystemExit(1)


def provider_span(text, provider):
    return re.search(r'^(\[model_providers\.' + re.escape(provider) + r'\]\s*$(.*?)(?=^\[|\Z))',
                     text, re.M | re.S)


def probe(url, payload=None):
    """返回 (状态码, content-type, 响应体前 200 字节)。"""
    body_fd, body_path = tempfile.mkstemp()
    os.close(body_fd)
    head_fd, head_path = tempfile.mkstemp()
    os.close(head_fd)
    cmd = ['curl', '-s', '-o', body_path, '-D', head_path, '-w', '%{http_code}', '--max-time', '15']
    if payload is not None:
        cmd += ['-X', 'POST', '-H', 'Content-Type: application/json', '-d', payload]
    cmd += [url, '-H', 'Authorization: Bearer ' + key]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
        code = (proc.stdout or '').strip() or '000'
    except (OSError, subprocess.SubprocessError):
        code = '000'
    try:
        head = open(head_path, encoding='utf-8', errors='ignore').read()
        body = open(body_path, encoding='utf-8', errors='ignore').read(200)
    except OSError:
        head, body = '', ''
    finally:
        for path in (body_path, head_path):
            try:
                os.remove(path)
            except OSError:
                pass
    ctype = ''
    for line in head.splitlines():
        if line.lower().startswith('content-type:'):
            ctype = line.split(':', 1)[1].strip().lower()
    return code, ctype, body


text = open(config_path, encoding='utf-8').read()
prov = re.search(r'^model_provider\s*=\s*"([^"\n]+)"', text, re.M)
provider = prov.group(1) if prov else 'token_shark'
span = provider_span(text, provider)
if not span:
    fail(f'配置里没有 [model_providers.{provider}] 段')
block = span.group(1)
bm = re.search(r'^base_url\s*=\s*"([^"\n]+)"', block, re.M)
if not bm:
    fail('读不到 base_url')
base = bm.group(1).rstrip('/')
mm = re.search(r'^model\s*=\s*"([^"\n]+)"', text, re.M)
model = mm.group(1) if mm else ''

code, ctype, body = probe(base + '/models')
sys.stderr.write(f'预检 GET {base}/models -> HTTP {code} ({ctype or "无 content-type"})\n')
if code != '200' or not body.lstrip().startswith('{'):
    fail('网关不可达或返回的不是 JSON：Token鲨 网关只对平台内网/校园网出口 IP 开放，'
         '本机不可达时不要强行切换。')


def route_exists(path, payload):
    c, ct, b = probe(base + path, payload)
    sys.stderr.write(f'探测 POST {base}{path} -> HTTP {c} ({ct or "无 content-type"})\n')
    if c in ('000', '404', '405'):
        return False
    if c == '200' and 'html' in ct:
        return False  # 命中了前端兜底页，不是 API 路由
    return True


resp_ok = route_exists('/responses', json.dumps({'model': model, 'input': []}))
chat_ok = route_exists('/chat/completions', json.dumps({'model': model, 'messages': []}))
if resp_ok:
    wire = 'responses'
elif chat_ok:
    wire = 'chat'
else:
    fail('网关既没有 /responses 也没有 /chat/completions，Codex 无法驱动它。')

# 删除 [model_providers.X.auth] 段：Codex 的 model provider 没有 auth 字段，
# 写了会被静默忽略 —— 等于没有凭据，平台会返回 401。
# 按行删：段里的数组参数（args = [...]）也带方括号，不能用正则一刀切。
def strip_auth_sections(text):
    out, skipping = [], False
    for line in text.splitlines(keepends=True):
        if re.match(r'^\s*\[[A-Za-z_][\w.\-]*(\.[\w.\-]*)*\.auth\]\s*$', line):
            skipping = True
            continue
        if skipping:
            if re.match(r'^\[[A-Za-z_][\w.\-]*(\.[\w.\-]*)*\]\s*$', line):
                skipping = False
            else:
                continue
        out.append(line)
    return ''.join(out)


text = strip_auth_sections(text)


def set_provider_value(text, provider, field, value):
    span = provider_span(text, provider)
    if not span:
        return text
    block = span.group(1)
    quoted = value.replace('\\', '\\\\').replace('"', '\\"')
    new_block, count = re.subn(r'^(\s*' + re.escape(field) + r'\s*=\s*)"[^"\n]*"',
                               lambda m: m.group(1) + '"' + quoted + '"', block, flags=re.M)
    if count == 0:
        new_block = new_block.rstrip('\n') + f'\n{field} = "{quoted}"\n'
    return text.replace(block, new_block, 1)


text = set_provider_value(text, provider, 'experimental_bearer_token', key)
text = set_provider_value(text, provider, 'wire_api', wire)

if os.path.exists(catalog_path):
    if re.search(r'^\s*model_catalog_json\s*=', text, re.M):
        text = re.sub(r'^(\s*model_catalog_json\s*=\s*)"[^"\n]*"',
                      lambda m: m.group(1) + '"' + catalog_path + '"', text, flags=re.M)
    else:
        # 顶层键必须放在第一个 [段] 之前
        cut = re.search(r'^\[', text, re.M)
        at = cut.start() if cut else len(text)
        text = text[:at] + f'model_catalog_json = "{catalog_path}"\n' + text[at:]
else:
    text = re.sub(r'^\s*model_catalog_json\s*=\s*"[^"\n]*"\n', '', text, flags=re.M)
    sys.stderr.write(f'警告：模型目录 {catalog_path} 不存在，已从配置中去掉 model_catalog_json\n')

with open(out_path, 'w', encoding='utf-8') as fh:
    fh.write(text)
os.chmod(out_path, 0o600)
print(wire)
PYEOF
)"; then
    python3 -c 'import os, sys; os.remove(sys.argv[1])' "$RENDERED" 2>/dev/null || true
    RENDERED=""
    exit 4
  fi
  CONFIG="$RENDERED"
  echo "✅ Token鲨 网关可达，协议 wire_api = \"$TS_WIRE\""
  unset TS_KEY
fi

python3 - "$CODEX_HOME" <<'PYEOF'
import os, shutil, sys
for root in ('sessions', 'archived_sessions'):
    for parent, _, names in os.walk(os.path.join(sys.argv[1], root)):
        for name in names:
            if not name.endswith('.jsonl.zst'):
                continue
            if not shutil.which('zstd'):
                raise SystemExit('压缩会话需要 zstd；请先安装，未更改任何数据')
            if os.path.exists(os.path.join(parent, name[:-4])):
                raise SystemExit('发现同名压缩和未压缩会话，未更改任何数据：' + os.path.join(parent, name))
PYEOF

echo "正在退出 Codex 应用..."
osascript -e "tell application \"$CODEX_APP\" to quit" 2>/dev/null || true
sleep 3

if [ "$MODE" = "kimi" ]; then
  echo "确保 Kimi 本地路由运行中..."
  # schema 垫片同步到 ~/.codex（修 $ref 兄弟字段 + 清理空 assistant 条目）
  [ -f "$DIR/schema-shim-kimi.py" ] && cp "$DIR/schema-shim-kimi.py" "$CODEX_HOME/schema-shim-kimi.py"
  launchctl kickstart -k "gui/$(id -u)/$LAUNCHD_LABEL" 2>/dev/null || \
    launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/$LAUNCHD_LABEL.plist" 2>/dev/null || true
  launchctl kickstart -k "gui/$(id -u)/$SHIM_LABEL" 2>/dev/null || \
    launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/$SHIM_LABEL.plist" 2>/dev/null || true
  sleep 2
fi

echo "写入 $MODE 配置..."
[ ! -f "$CODEX_HOME/config.toml" ] || cp -p "$CODEX_HOME/config.toml" "$CODEX_HOME/config.toml.bak-switch"
[ ! -f "$CODEX_HOME/config.toml.bak-switch" ] || chmod 600 "$CODEX_HOME/config.toml.bak-switch"
cp "$CONFIG" "$CODEX_HOME/config.toml"
chmod 600 "$CODEX_HOME/config.toml"

echo "同步会话显示标记与默认模型..."
# 预设各模式默认模型与强度，切换后无需在界面重新选择（2026-09-18 新增）：
# 除新会话默认（config.toml 的 model 字段）外，还需把 threads 表里每个会话钉死的
# model/reasoning_effort 一并翻转，否则打开旧会话仍用旧模型执行失败。
#
# provider 名称必须与目标配置里的 model_provider 完全一致，否则桌面端 app-server
# 会拒绝打开会话并报 modelProviderMismatch：
#   "This chat uses custom, but your current provider is deepseek."
# 会话标记与配置两处名字只要有一个对不上，桌面端就是「继续聊天未成功」。
# 因此这里从配置模板里读出实际使用的 provider 名（第三方模式通常是 custom，
# 但用户可能改成 deepseek / kimi 等自定义名），并把所有会话标记统一刷成它。
TARGET_PROVIDER=$(python3 - "$CONFIG" <<'PYEOF'
import re, sys
text = open(sys.argv[1], encoding='utf-8').read()
match = re.search(r'^model_provider\s*=\s*"([^"\n]+)"', text, re.M)
print(match.group(1) if match else 'openai')
PYEOF
)
echo "目标 provider 名称: $TARGET_PROVIDER"
python3 - "$CODEX_HOME" "$TARGET_PROVIDER" "$DEF_MODEL" "$DEF_EFFORT" <<'PYEOF'
import sqlite3, sys, os, subprocess, time
home, to, model, effort = sys.argv[1:]
db = os.path.join(home, 'state_5.sqlite')
if not os.path.exists(db):
    print("未找到 state_5.sqlite，跳过"); raise SystemExit
con = sqlite3.connect(db)
with sqlite3.connect(db + '.bak-switch') as backup:
    con.backup(backup)
os.chmod(db + '.bak-switch', 0o600)
decompressed = 0
for root in ('sessions', 'archived_sessions'):
    for parent, _, names in os.walk(os.path.join(home, root)):
        for name in names:
            if not name.endswith('.jsonl.zst'):
                continue
            compressed = os.path.join(parent, name)
            plain = compressed[:-4]
            if os.path.exists(plain):
                continue
            temp = plain + '.tmp-switch-zstd'
            try:
                with open(temp, 'wb') as out:
                    os.chmod(temp, 0o600)
                    subprocess.run(['zstd', '-dc', compressed], stdout=out, check=True)
                if not os.path.getsize(temp):
                    raise ValueError('压缩会话解压结果为空')
                os.replace(temp, plain)
                backup = compressed + '.bak-switch'
                if os.path.exists(backup):
                    backup += '.' + str(time.time_ns())
                os.replace(compressed, backup)
                con.execute('update threads set rollout_path=? where rollout_path=?', (plain, compressed))
                decompressed += 1
            finally:
                if os.path.exists(temp):
                    os.remove(temp)
print(f'已解压 {decompressed} 个会话并保留压缩备份')
# 把所有会话的 provider 标记统一刷成目标名称：不只是 openai <-> custom 这一对，
# 任何与目标不符的标记都会让桌面端拒绝打开该会话。
before = dict(con.execute('select model_provider, count(*) from threads group by model_provider').fetchall())
cur = con.execute('update threads set model_provider=? where model_provider<>?', (to, to))
if before.get(to):
    print(f"已更新 {cur.rowcount} 条会话标记 -> {to}（该名称原有 {before[to]} 条）")
else:
    print(f"已更新 {cur.rowcount} 条会话标记 -> {to}（原分布 {before}）")
cur2 = con.execute("update threads set model=?, reasoning_effort=?", (model, effort))
print(f"已预设 {cur2.rowcount} 条会话的默认模型: {model}（强度 {effort}）")
con.commit(); con.close()
PYEOF

echo "同步 CC Switch 启用标记（如已安装）..."
python3 - "$MODE" <<'PYEOF'
import sqlite3, sys, os
mode = sys.argv[1]
# 按供应商名字模糊匹配，不同安装里 UUID 各不相同
kw = {'openai': 'openai%', 'deepseek': 'deepseek%', 'kimi': 'kimi%', 'token_shark': 'token_shark%'}[mode]
db = os.path.expanduser('~/.cc-switch/cc-switch.db')
if not os.path.exists(db):
    print("未安装 CC Switch，跳过"); raise SystemExit
try:
    con = sqlite3.connect(db)
    con.execute("update providers set is_current=0 where app_type='codex'")
    cur = con.execute("update providers set is_current=1 where app_type='codex' and lower(name) like ?", (kw,))
    con.commit(); con.close()
    print(f"CC Switch 标记已同步: {mode}（匹配 {cur.rowcount} 个供应商）")
except sqlite3.Error as e:
    print(f"CC Switch db 结构不兼容，跳过: {e}")
PYEOF

# 增量改写：两趟改写各自记录上次处理时间，只扫之后变动过的文件。
# 改写均为幂等，归档会话处理一次即不再变化，活跃会话只补扫新增部分
# （2026-09-18 改造：全量扫描让切换随会话积累越来越慢）。
MARKER_MODEL="$CODEX_HOME/.switch-rewrite-marker-model"
MARKER_REASONING="$CODEX_HOME/.switch-rewrite-marker-reasoning"
SINCE_MODEL=$(cat "$MARKER_MODEL" 2>/dev/null || echo 0)
SINCE_REASONING=$(cat "$MARKER_REASONING" 2>/dev/null || echo 0)
# 每次切换供应商都必须重新扫描：未修改的旧会话也可能属于另一供应商。
# 标记里带上 provider 名称——只改配置里的 provider 名字（如 custom 改成 deepseek）
# 也必须触发全量扫描，否则 rollout 里的旧 provider 标记不会被改写。
if [ "$(cat "$CODEX_HOME/.switch-model-mode" 2>/dev/null || true)" != "$MODE:$DEF_MODEL:$TARGET_PROVIDER" ]; then
  SINCE_MODEL=0
fi
NOW_TS=$(python3 -c 'import time; print(time.time())')

echo "改写历史会话模型名与 provider 标记（解决续聊 compact 钉死模型名问题）..."
# codex 续聊时的 pre-sampling compact 使用会话 rollout 元数据里记录的模型名，
# 与当前配置无关。跨供应商续聊时对方 API 不认识该名字会报 invalid_request_error
# （在 codex 0.153.4 实测确认）。这里把历史里的模型名统一改写成目标供应商
# 的模型名。只动模型归属元数据，会话内容不受影响；每个文件首次改写前留 .bak-model。
#
# rollout 的 session_meta.model_provider 同样要跟着改：app-server 打开会话时会
# 拿它和当前配置的 model_provider 比对，不一致就报 modelProviderMismatch
# （"This chat uses openai, but your current provider is deepseek"），
# 桌面端表现为「继续聊天未成功」。只改数据库 threads 表是不够的。
python3 - "$MODE" "$CODEX_HOME" "$SINCE_MODEL" "$DEF_MODEL" "$TARGET_PROVIDER" <<'PYEOF'
import json, os, re, sys, shutil

mode, home, since, default, provider = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4], sys.argv[5]
# 各供应商认识的模型名（不在白名单里的会被改写成默认名，按需修改）
cfg = {
    'openai':   re.compile(r'^gpt-'),
    'deepseek': re.compile(r'^deepseek-'),
    'kimi':     re.compile(r'^(kimi-|k3)'),
    'token_shark': re.compile(r'^(GLM-|Qwen|Deepseek-)'),
}
keep = cfg[mode]
paths = {
    'session_meta': [('base_instructions', 'provenance', 'model')],
    'world_state': [('state', 'model'), ('state', 'collaboration_mode', 'model')],
    'turn_context': [('model',), ('collaboration_mode', 'settings', 'model')],
    'event_msg': [('thread_settings', 'model'), ('thread_settings', 'collaboration_mode', 'settings', 'model')],
}

def rewrite(line):
    try:
        item = json.loads(line)
    except (ValueError, TypeError):
        return line, set()
    if not isinstance(item, dict):
        return line, set()
    names = set()
    for path in paths.get(item.get('type'), ()):
        node = item.get('payload')
        for part in path[:-1]:
            node = node.get(part) if isinstance(node, dict) else None
        name = node.get(path[-1]) if isinstance(node, dict) else None
        if isinstance(name, str) and not keep.match(name):
            names.add(name)
            node[path[-1]] = default
    # provider 标记与当前配置对齐，否则 app-server 拒绝打开该会话
    if item.get('type') == 'session_meta':
        payload = item.get('payload')
        if isinstance(payload, dict):
            current = payload.get('model_provider')
            if isinstance(current, str) and current != provider:
                names.add(f'provider:{current}')
                payload['model_provider'] = provider
    return (json.dumps(item, ensure_ascii=False, separators=(',', ':')) + '\n', names) if names else (line, names)

roots = [os.path.join(home, 'sessions'), os.path.join(home, 'archived_sessions')]
changed_files, changed_names, changed_paths = 0, set(), []

for root in roots:
    if not os.path.isdir(root):
        continue
    for dirpath, _, files in os.walk(root):
        for fn in files:
            if not fn.endswith('.jsonl'):
                continue
            p = os.path.join(dirpath, fn)
            try:
                if os.path.getmtime(p) <= since:
                    continue  # 增量：上次处理后未变动过
            except OSError:
                continue
            try:
                tmp = p + '.tmp-switch'
                file_names = set()
                with open(p, 'r', encoding='utf-8') as fin, open(tmp, 'w', encoding='utf-8') as fout:
                    os.chmod(tmp, 0o600)
                    for line in fin:
                        rewritten, names = rewrite(line)
                        fout.write(rewritten)
                        file_names.update(names)
                if not file_names:
                    os.remove(tmp)
                    continue
                bak = p + '.bak-model'
                if not os.path.exists(bak):
                    shutil.copy(p, bak)  # 只保留首次备份，避免覆盖原始版本
                os.replace(tmp, p)
                changed_names.update(file_names)
                changed_files += 1
                changed_paths.append(p)
            except (OSError, UnicodeDecodeError):
                continue

if changed_files:
    models_changed = sorted(n for n in changed_names if not n.startswith('provider:'))
    providers_changed = sorted(n for n in changed_names if n.startswith('provider:'))
    detail = []
    if models_changed:
        detail.append(f"模型名 {models_changed} -> {default}")
    if providers_changed:
        detail.append(f"provider 标记 {[p.split(':', 1)[1] for p in providers_changed]} -> {provider}")
    print(f"已改写 {changed_files} 个会话文件: " + "；".join(detail))
else:
    print("无需改写（历史会话模型名与 provider 标记均已匹配）")

# 记录被改写的线程 id，供最后统一失效 GUI 投影缓存
uids = re.findall(r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$', '\n'.join(changed_paths), re.M)
if uids:
    with open(os.path.join(home, '.switch-rewritten-threads'), 'a') as fh:
        fh.write('\n'.join(uids) + '\n')
PYEOF
echo "$NOW_TS" > "$MARKER_MODEL"
echo "$MODE:$DEF_MODEL:$TARGET_PROVIDER" > "$CODEX_HOME/.switch-model-mode"

if [ "$MODE" = "openai" ]; then
  echo "清理第三方会话残留（解决官方续聊校验报错）..."
  # 切回官方前对历史会话做三类清理（均为实测确认的兼容问题）：
  # 1. reasoning 项剥离 content 数组和本地 Fernet 加密的 encrypted_content
  #    —— 否则官方 API 报 array_above_max_length / invalid_encrypted_content
  # 2. reasoning 项剥离 id —— 第三方产生的 rs_ id 在官方侧无存储记录，
  #    重放被当作引用解析报 "Item with id 'rs_...' not found"（store=false）
  # 3. 工具调用 id/call_id 的 tool_ 等前缀改成 fc 前缀
  #    —— 否则官方报 invalid_id_prefix: Expected an ID that begins with 'fc'
  # 剥离后官方只拿到 summary，可正常续聊；首次清理前留 .bak-reasoning。
  python3 - "$CODEX_HOME" "$SINCE_REASONING" <<'PYEOF'
import os, sys, json, re, shutil

home, since = sys.argv[1], float(sys.argv[2])
roots = [os.path.join(home, 'sessions'), os.path.join(home, 'archived_sessions')]
ID_PAT = re.compile(r'"(id|call_id)":\s*"tool_')

def clean(node, stats):
    if isinstance(node, dict):
        if node.get('type') in ('reasoning', 'Reasoning'):
            for k in ('content', 'encrypted_content', 'id'):
                if k in node:
                    del node[k]; stats[k] += 1
        for v in node.values():
            clean(v, stats)
    elif isinstance(node, list):
        for v in node:
            clean(v, stats)

changed = 0; changed_paths = []; total = {'content': 0, 'encrypted_content': 0, 'id': 0, 'fc_prefix': 0}
for root in roots:
    if not os.path.isdir(root):
        continue
    for dirpath, _, files in os.walk(root):
        for fn in files:
            if not fn.endswith('.jsonl'):
                continue
            p = os.path.join(dirpath, fn)
            try:
                if os.path.getmtime(p) <= since:
                    continue  # 增量：上次处理后未变动过
            except OSError:
                continue
            # 流式逐行转换到临时文件，全程不把整个文件读进内存
            stats = {'content': 0, 'encrypted_content': 0, 'id': 0, 'fc_prefix': 0}
            tmp = p + '.tmp-switch'
            try:
                with open(p, encoding='utf-8', errors='ignore') as fin, \
                     open(tmp, 'w', encoding='utf-8') as fout:
                    os.chmod(tmp, 0o600)
                    for line in fin:
                        # 规则 3：工具调用 ID 前缀（纯文本替换，覆盖所有嵌套副本）
                        line, n = ID_PAT.subn(r'"\1": "fc', line)
                        stats['fc_prefix'] += n
                        if 'reasoning' not in line and 'Reasoning' not in line:
                            fout.write(line); continue
                        try:
                            d = json.loads(line)
                            clean(d, stats)
                            fout.write(json.dumps(d, ensure_ascii=False) + '\n')
                        except Exception:
                            fout.write(line)
            except OSError:
                try: os.remove(tmp)
                except OSError: pass
                continue
            if any(stats.values()):
                bak = p + '.bak-reasoning'
                if not os.path.exists(bak):
                    shutil.copy(p, bak)
                os.replace(tmp, p)
                changed += 1
                changed_paths.append(p)
                for k in total:
                    total[k] += stats[k]
            else:
                os.remove(tmp)

if changed:
    print(f"已清理 {changed} 个会话文件: content {total['content']} 处, encrypted_content {total['encrypted_content']} 处, reasoning id {total['id']} 处, 工具 ID 前缀 {total['fc_prefix']} 处")
else:
    print("无需清理（无第三方会话残留）")

# 记录被清理的线程 id，供最后统一失效 GUI 投影缓存
uids = re.findall(r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$', '\n'.join(changed_paths), re.M)
if uids:
    with open(os.path.join(home, '.switch-rewritten-threads'), 'a') as fh:
        fh.write('\n'.join(uids) + '\n')
PYEOF
  echo "$NOW_TS" > "$MARKER_REASONING"
fi

# 让 GUI 的对话投影缓存（thread_history_1.sqlite）对被改写的线程失效。
# 投影按字节偏移索引 rollout 文件，原地改写会让 GUI 停在旧位置、显示过期内容
# （实测确认）；删除投影行后 app 会从 jsonl 自动重建。
if [ -f "$CODEX_HOME/.switch-rewritten-threads" ]; then
  echo "失效被改写线程的 GUI 投影缓存..."
  python3 - "$CODEX_HOME" <<'PYEOF'
import os, sys, sqlite3
home = sys.argv[1]
flag = os.path.join(home, '.switch-rewritten-threads')
with open(flag) as fh:
    uids = sorted(set(u.strip() for u in fh if u.strip()))
db = os.path.join(home, 'thread_history_1.sqlite')
if uids and os.path.exists(db):
    con = sqlite3.connect(db)
    for t in ('thread_items', 'thread_turns', 'thread_history_projection_state', 'thread_realtime_items'):
        con.execute(f"delete from {t} where thread_id in ({','.join('?' * len(uids))})", uids)
    con.commit(); con.close()
    print(f"已失效 {len(uids)} 个线程的投影缓存（重启后自动重建）")
os.remove(flag)
PYEOF
fi

# 重映射分叉会话的 history_base 字节偏移。
# 上面的改写会改变 rollout 文件长度，而分叉文件头部内嵌的
# history_base.end_byte_offset 仍指向改写前的字节位置；偏移越界时 App 报
# "invalid paginated history lineage: cutoff byte offset is past the source rollout"
# 且无法失效缓存自救（偏移在 jsonl 里，不在数据库）。按 ordinal 重新定位同一行
# 在当前源文件中的字节位置并更新（2026-09-18 实测确认并修复）。
echo "重映射分叉会话的 history_base 偏移..."
python3 "$DIR/remap-history-base.py" "$CODEX_HOME"

echo "Codex CLI 版本: $("$CODEX_CLI" --version 2>/dev/null | head -1 || echo 未知)"

echo "重启 Codex 应用..."
open -a "$CODEX_APP"
echo "完成！当前模式: $MODE"
