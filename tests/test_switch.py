import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SwitchingTest(unittest.TestCase):
    def test_repeated_switch_rewrites_unchanged_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / 'codex'
            sessions = home / 'sessions'
            sessions.mkdir(parents=True)
            (home / 'switch-config-deepseek.toml').write_text(
                (ROOT / 'config-deepseek.toml').read_text().replace('YOUR_DEEPSEEK_API_KEY', 'test-key'))
            bin_dir = root / 'bin'
            bin_dir.mkdir()
            for name in ('osascript', 'sleep', 'open'):
                path = bin_dir / name
                path.write_text('#!/bin/sh\nexit 0\n')
                path.chmod(0o755)
            db = sqlite3.connect(home / 'state_5.sqlite')
            db.execute('pragma journal_mode=wal')
            db.execute('pragma wal_autocheckpoint=0')
            db.execute('create table threads (model_provider text, model text, reasoning_effort text)')
            db.execute("insert into threads values ('openai', 'gpt-6-luna', 'medium')")
            db.commit()
            rollout = sessions / 'rollout.jsonl'
            metadata = {'type': 'turn_context', 'payload': {'model': 'gpt-6-luna'}}
            message = {'type': 'response_item', 'payload': {'type': 'message', 'model': 'my-data'}}
            other = [
                {'type': 'session_meta', 'payload': {'base_instructions': {'provenance': {'model': 'gpt-6-luna'}}}},
                {'type': 'world_state', 'payload': {'state': {'model': 'gpt-6-luna', 'collaboration_mode': {'model': 'gpt-6-luna'}}}},
                {'type': 'event_msg', 'payload': {'thread_settings': {'model': 'gpt-6-luna'}}},
            ]
            rollout.write_text(''.join(json.dumps(v, separators=(',', ':')) + '\n' for v in (metadata, message, *other)))
            def records():
                return [json.loads(line) for line in rollout.read_text().splitlines()]
            env = dict(os.environ, HOME=str(root), CODEX_HOME=str(home),
                       PATH=str(bin_dir) + os.pathsep + os.environ['PATH'])
            def switch(mode):
                subprocess.run(['bash', str(ROOT / 'codex-switch.sh'), mode], env=env,
                               check=True, capture_output=True, text=True)
            switch('openai')
            switch('deepseek')
            with sqlite3.connect(home / 'state_5.sqlite.bak-switch') as backup:
                self.assertEqual(backup.execute('select count(*) from threads').fetchone()[0], 1)
            self.assertEqual(records()[0]['payload']['model'], 'deepseek-flash')
            self.assertEqual(records()[1], message)
            db.close()
            self.assertEqual(records()[2]['payload']['base_instructions']['provenance']['model'], 'deepseek-flash')
            self.assertEqual(records()[3]['payload']['state']['collaboration_mode']['model'], 'deepseek-flash')
            self.assertEqual(records()[4]['payload']['thread_settings']['model'], 'deepseek-flash')
            with sqlite3.connect(home / 'state_5.sqlite') as db:
                self.assertEqual(db.execute('select model, reasoning_effort from threads').fetchone(),
                                 ('deepseek-flash', 'high'))
            self.assertIn('gpt-5.6-terra', (home / 'config.toml.bak-switch').read_text())
            switch('openai')
            self.assertEqual(records()[0]['payload']['model'], 'gpt-5.6-terra')
            switch('deepseek')
            self.assertEqual(records()[0]['payload']['model'], 'deepseek-flash')
            self.assertEqual(records()[1], message)

    def test_missing_key_stops_before_changing_config(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            config = home / 'config.toml'
            config.write_text('model = "original"\n')
            env = dict(os.environ, HOME=temp, CODEX_HOME=temp)
            result = subprocess.run(['bash', str(ROOT / 'codex-switch.sh'), 'deepseek'],
                                    env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('密钥尚未配置', result.stderr)
            self.assertEqual(config.read_text(), 'model = "original"\n')
            self.assertFalse((home / 'config.toml.bak-switch').exists())

    @unittest.skipUnless(shutil.which('zstd'), 'zstd is required')
    def test_compressed_history_is_rewritten_and_backed_up(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / 'codex'
            sessions = home / 'archived_sessions'
            sessions.mkdir(parents=True)
            (home / 'switch-config-deepseek.toml').write_text(
                (ROOT / 'config-deepseek.toml').read_text().replace('YOUR_DEEPSEEK_API_KEY', 'test-key'))
            thread_id = '12345678-1234-1234-1234-123456789abc'
            plain = sessions / f'rollout-2026-01-01-{thread_id}.jsonl'
            lines = [
                '{"ordinal":0,"type":"session_meta","payload":{}}\n',
                '{"ordinal":1,"type":"turn_context","payload":{"model":"gpt-6-luna"}}\n',
                '{"ordinal":2,"type":"event_msg","payload":{}}\n',
            ]
            plain.write_text(''.join(lines))
            fork = sessions / f'rollout-2026-01-02-{thread_id}.jsonl'
            fork.write_text(json.dumps({'type': 'session_meta', 'payload': {'history_base': {
                'thread_id': thread_id, 'end_ordinal_exclusive': 2,
                'end_byte_offset': len(''.join(lines[:2]).encode()),
            }}}, separators=(',', ':')) + '\n')
            subprocess.run(['zstd', '-q', '--rm', str(plain)], check=True)
            compressed = Path(str(plain) + '.zst')
            with sqlite3.connect(home / 'state_5.sqlite') as db:
                db.execute('create table threads (model_provider text, model text, reasoning_effort text, rollout_path text)')
                db.execute('insert into threads values (?, ?, ?, ?)', ('openai', 'gpt-6-luna', 'medium', str(compressed)))
            bin_dir = root / 'bin'
            bin_dir.mkdir()
            for name in ('osascript', 'sleep', 'open'):
                path = bin_dir / name
                path.write_text('#!/bin/sh\nexit 0\n')
                path.chmod(0o755)
            env = dict(os.environ, HOME=str(root), CODEX_HOME=str(home),
                       PATH=str(bin_dir) + os.pathsep + os.environ['PATH'])
            subprocess.run(['bash', str(ROOT / 'codex-switch.sh'), 'deepseek'], env=env,
                           check=True, capture_output=True, text=True)
            rewritten_lines = plain.read_bytes().splitlines(keepends=True)
            self.assertEqual(json.loads(rewritten_lines[1])['payload']['model'], 'deepseek-flash')
            cutoff = json.loads(fork.read_text())['payload']['history_base']['end_byte_offset']
            self.assertEqual(cutoff, len(b''.join(rewritten_lines[:2])))
            self.assertTrue(Path(str(compressed) + '.bak-switch').exists())
            with sqlite3.connect(home / 'state_5.sqlite') as db:
                self.assertEqual(db.execute('select rollout_path from threads').fetchone()[0], str(plain))

    @unittest.skipUnless(shutil.which('zstd'), 'zstd is required')
    def test_duplicate_compressed_history_stops_before_switch(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            (home / 'switch-config-deepseek.toml').write_text(
                (ROOT / 'config-deepseek.toml').read_text().replace('YOUR_DEEPSEEK_API_KEY', 'test-key'))
            (home / 'sessions').mkdir()
            (home / 'sessions' / 'rollout.jsonl').write_text('original\n')
            (home / 'sessions' / 'rollout.jsonl.zst').write_bytes(b'other')
            (home / 'config.toml').write_text('model = "original"\n')
            env = dict(os.environ, HOME=temp, CODEX_HOME=temp)
            result = subprocess.run(['bash', str(ROOT / 'codex-switch.sh'), 'deepseek'],
                                    env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('同名压缩和未压缩会话', result.stderr)
            self.assertEqual((home / 'config.toml').read_text(), 'model = "original"\n')

    def test_provider_markers_follow_configured_provider_name(self):
        """桌面端按 provider 名称匹配会话，配置改名后必须同步数据库与 rollout 标记。

        回归场景：配置里的 model_provider 从 custom 改成 deepseek，数据库里仍写着
        custom，桌面端报 modelProviderMismatch（"This chat uses custom, but your
        current provider is deepseek"），所有历史会话都打不开。
        """
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / 'codex'
            sessions = home / 'sessions'
            sessions.mkdir(parents=True)
            # 具名 provider，且模型名不是 gpt- 开头（验证白名单逻辑按目标供应商判断）
            (home / 'switch-config-deepseek.toml').write_text(
                'model_provider = "deepseek"\n'
                'model = "deepseek-flash"\n'
                'model_reasoning_effort = "high"\n'
                '[model_providers.deepseek]\n'
                'name = "DeepSeek"\n'
                'base_url = "https://api.deepseek.com"\n'
                'wire_api = "responses"\n')
            bin_dir = root / 'bin'
            bin_dir.mkdir()
            for name in ('osascript', 'sleep', 'open'):
                path = bin_dir / name
                path.write_text('#!/bin/sh\nexit 0\n')
                path.chmod(0o755)
            db = sqlite3.connect(home / 'state_5.sqlite')
            db.execute('create table threads (model_provider text, model text, reasoning_effort text)')
            db.execute("insert into threads values ('custom', 'deepseek-flash', 'high')")
            db.execute("insert into threads values ('openai', 'gpt-6-luna', 'medium')")
            db.commit()
            db.close()
            rollout = sessions / 'rollout.jsonl'
            rollout.write_text(json.dumps({
                'type': 'session_meta',
                'payload': {
                    'model_provider': 'custom',
                    'base_instructions': {'provenance': {'model': 'deepseek-flash'}},
                },
            }, separators=(',', ':')) + '\n')
            env = dict(os.environ, HOME=str(root), CODEX_HOME=str(home),
                       PATH=str(bin_dir) + os.pathsep + os.environ['PATH'])
            subprocess.run(['bash', str(ROOT / 'codex-switch.sh'), 'deepseek'], env=env,
                           check=True, capture_output=True, text=True)
            # 数据库里的所有标记都要变成配置里的 provider 名
            with sqlite3.connect(home / 'state_5.sqlite') as db:
                providers = {row[0] for row in db.execute('select model_provider from threads')}
            self.assertEqual(providers, {'deepseek'})
            # rollout 的 session_meta.model_provider 也要改，否则 app-server 仍会拒绝打开
            payload = json.loads(rollout.read_text())['payload']
            self.assertEqual(payload['model_provider'], 'deepseek')
            self.assertEqual(payload['base_instructions']['provenance']['model'], 'deepseek-flash')

    def test_check_mode_reports_provider_mismatch(self):
        """check 子命令只读诊断，必须能报出会话标记与配置不一致。"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / 'codex'
            home.mkdir(parents=True)
            (home / 'config.toml').write_text(
                'model_provider = "custom"\nmodel = "deepseek-flash"\n')
            db = sqlite3.connect(home / 'state_5.sqlite')
            db.execute('create table threads (model_provider text, model text, reasoning_effort text)')
            db.execute("insert into threads values ('openai', 'gpt-6-luna', 'medium')")
            db.commit()
            db.close()
            env = dict(os.environ, HOME=str(root), CODEX_HOME=str(home))
            result = subprocess.run(['bash', str(ROOT / 'codex-switch.sh'), 'check'],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            self.assertIn('与当前配置', result.stdout)
            self.assertIn('modelProviderMismatch', result.stdout)


# ---- token_shark（校园 LLM 网关）模式 ----

STUB_CURL = r'''#!/bin/sh
out=""; hdr=""; url=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2;;
    -D) hdr="$2"; shift 2;;
    -w|-X|-H|-d|--max-time) shift 2;;
    *) url="$1"; shift;;
  esac
done
case "$url" in
  */models) code="${STUB_MODELS_CODE:-200}"; body='{"object":"list","data":[]}';;
  */responses) code="${STUB_RESPONSES_CODE:-400}"; body='{"error":"probe"}';;
  */chat/completions) code="${STUB_CHAT_CODE:-404}"; body='{"error":"probe"}';;
  *) code="${STUB_OTHER_CODE:-404}"; body='{}';;
esac
[ -n "$hdr" ] && printf 'HTTP/1.1 %s\r\nContent-Type: application/json\r\n\r\n' "$code" > "$hdr"
[ -n "$out" ] && printf '%s' "$body" > "$out"
printf '%s' "$code"
'''

STUB_SECURITY = r'''#!/bin/sh
if [ "$1" = "find-generic-password" ]; then
  printf 'sk-stub-token'
  exit 0
fi
exit 1
'''


class TokenSharkTest(unittest.TestCase):
    """Token鲨 模式：钥匙串取密钥、网关预检、协议探测、provider 标记同步。"""

    def _prepare(self, root):
        home = root / 'codex'
        (home / 'sessions').mkdir(parents=True)
        (home / 'switch-config-token-shark.toml').write_text(
            (ROOT / 'config-token-shark.toml').read_text())
        (home / 'token-shark-models.json').write_text('{"models": []}')
        bin_dir = root / 'bin'
        bin_dir.mkdir()
        for name, body in (('osascript', '#!/bin/sh\nexit 0\n'),
                           ('sleep', '#!/bin/sh\nexit 0\n'),
                           ('open', '#!/bin/sh\nexit 0\n'),
                           ('security', STUB_SECURITY),
                           ('curl', STUB_CURL)):
            path = bin_dir / name
            path.write_text(body)
            path.chmod(0o755)
        env = dict(os.environ, HOME=str(root), CODEX_HOME=str(home),
                   PATH=str(bin_dir) + os.pathsep + os.environ['PATH'])
        return home, env

    def test_switch_injects_key_and_aligns_provider_markers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home, env = self._prepare(root)
            db = sqlite3.connect(home / 'state_5.sqlite')
            db.execute('create table threads (model_provider text, model text, reasoning_effort text)')
            db.execute("insert into threads values ('custom', 'deepseek-flash', 'high')")
            db.commit()
            db.close()
            rollout = home / 'sessions' / 'rollout.jsonl'
            rollout.write_text(json.dumps({
                'type': 'session_meta',
                'payload': {
                    'model_provider': 'custom',
                    'base_instructions': {'provenance': {'model': 'deepseek-flash'}},
                },
            }, separators=(',', ':')) + '\n')
            result = subprocess.run(['bash', str(ROOT / 'codex-switch.sh'), 'token_shark'],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            config = (home / 'config.toml').read_text()
            # 密钥来自钥匙串，不是模板里的占位符
            self.assertIn('experimental_bearer_token = "sk-stub-token"', config)
            self.assertNotIn('TOKEN_SHARK_API_KEY', config)
            # 模型目录指向用户目录下的真实文件
            self.assertIn(f'model_catalog_json = "{home / "token-shark-models.json"}"', config)
            # 协议探测：/responses 存在 -> wire_api = responses
            self.assertIn('wire_api = "responses"', config)
            # provider 标记全部同步成 token_shark
            with sqlite3.connect(home / 'state_5.sqlite') as db:
                providers = {row[0] for row in db.execute('select model_provider from threads')}
            self.assertEqual(providers, {'token_shark'})
            payload = json.loads(rollout.read_text())['payload']
            self.assertEqual(payload['model_provider'], 'token_shark')

    def test_switch_falls_back_to_chat_completions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home, env = self._prepare(root)
            env['STUB_RESPONSES_CODE'] = '404'
            env['STUB_CHAT_CODE'] = '400'
            result = subprocess.run(['bash', str(ROOT / 'codex-switch.sh'), 'token_shark'],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('wire_api = "chat"', (home / 'config.toml').read_text())

    def test_unreachable_gateway_keeps_original_config(self):
        """网关不通必须中止：原配置保持不变，也不留下备份。"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home, env = self._prepare(root)
            env['STUB_MODELS_CODE'] = '000'
            (home / 'config.toml').write_text('model = "original"\n')
            result = subprocess.run(['bash', str(ROOT / 'codex-switch.sh'), 'token_shark'],
                                    env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('网关', result.stderr)
            self.assertEqual((home / 'config.toml').read_text(), 'model = "original"\n')
            self.assertFalse((home / 'config.toml.bak-switch').exists())

    def test_base_url_can_be_overridden_by_env(self):
        """平台改用 443 反代后，用环境变量换地址，不必改仓库模板。"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home, env = self._prepare(root)
            env['CODEX_SWITCH_TOKEN_SHARK_BASE_URL'] = 'https://llm.ahau.edu.cn/v1'
            result = subprocess.run(['bash', str(ROOT / 'codex-switch.sh'), 'token_shark'],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            config = (home / 'config.toml').read_text()
            self.assertIn('base_url = "https://llm.ahau.edu.cn/v1"', config)
            # 生效的 base_url 不能再指向平台内部网关（注释里提到它是为了提醒别用）
            self.assertNotRegex(config, r'^base_url\s*=\s*"[^"]*4000')

    def test_missing_keychain_entry_stops_before_changing_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home, env = self._prepare(root)
            (root / 'bin' / 'security').write_text('#!/bin/sh\nexit 1\n')
            (root / 'bin' / 'security').chmod(0o755)
            (home / 'config.toml').write_text('model = "original"\n')
            result = subprocess.run(['bash', str(ROOT / 'codex-switch.sh'), 'token_shark'],
                                    env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('钥匙串', result.stderr)
            self.assertEqual((home / 'config.toml').read_text(), 'model = "original"\n')

    def test_inline_key_is_used_when_keychain_is_empty(self):
        """钥匙串没有密钥时，回退到配置文件里内联的真实密钥（占位符不算）。"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home, env = self._prepare(root)
            (root / 'bin' / 'security').write_text('#!/bin/sh\nexit 1\n')
            (root / 'bin' / 'security').chmod(0o755)
            template = (home / 'switch-config-token-shark.toml').read_text().replace(
                'TOKEN_SHARK_API_KEY', 'sk-inline-key')
            (home / 'switch-config-token-shark.toml').write_text(template)
            result = subprocess.run(['bash', str(ROOT / 'codex-switch.sh'), 'token_shark'],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('experimental_bearer_token = "sk-inline-key"',
                          (home / 'config.toml').read_text())

    def test_auth_section_is_stripped(self):
        """[model_providers.X.auth] 不是 Codex 支持的字段，留着等于没有凭据。

        段里通常还有 args = [...] 这样的数组行（行首带方括号），
        用正则一刀切会把数组行留下来，写成非法 TOML，Codex 直接起不来。
        """
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home, env = self._prepare(root)
            template = (home / 'switch-config-token-shark.toml').read_text()
            template += ('\n[model_providers.token_shark.auth]\n'
                         'command = "/usr/bin/security"\n'
                         'args = ["find-generic-password", "-a", "token-shark",'
                         ' "-s", "codex-token-shark", "-w"]\n'
                         'timeout_ms = 5000\n'
                         'refresh_interval_ms = 0\n')
            (home / 'switch-config-token-shark.toml').write_text(template)
            result = subprocess.run(['bash', str(ROOT / 'codex-switch.sh'), 'token_shark'],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            rendered = (home / 'config.toml').read_text()
            self.assertNotIn('token_shark.auth', rendered)
            self.assertNotIn('find-generic-password', rendered)
            self.assertNotIn('refresh_interval_ms', rendered)
            # 删干净之后必须仍是合法 TOML
            tomllib.loads(rendered)


if __name__ == '__main__':
    unittest.main()
