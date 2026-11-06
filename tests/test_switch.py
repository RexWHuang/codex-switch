import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
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


if __name__ == '__main__':
    unittest.main()
