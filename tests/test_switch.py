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
            plain = sessions / 'rollout.jsonl'
            plain.write_text('{"type":"turn_context","payload":{"model":"gpt-6-luna"}}\n')
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
            self.assertEqual(json.loads(plain.read_text())['payload']['model'], 'deepseek-flash')
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


if __name__ == '__main__':
    unittest.main()
