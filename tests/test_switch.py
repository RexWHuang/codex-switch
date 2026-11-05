import json
import os
from pathlib import Path
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
            bin_dir = root / 'bin'
            bin_dir.mkdir()
            for name in ('osascript', 'sleep', 'open'):
                path = bin_dir / name
                path.write_text('#!/bin/sh\nexit 0\n')
                path.chmod(0o755)
            db = sqlite3.connect(home / 'state_5.sqlite')
            db.execute('create table threads (model_provider text, model text, reasoning_effort text)')
            db.execute("insert into threads values ('openai', 'gpt-6-luna', 'medium')")
            db.commit()
            db.close()
            rollout = sessions / 'rollout.jsonl'
            rollout.write_text(json.dumps({'model': 'gpt-6-luna'}, separators=(',', ':')) + '\n')
            env = dict(os.environ, HOME=str(root), CODEX_HOME=str(home),
                       PATH=str(bin_dir) + os.pathsep + os.environ['PATH'])
            def switch(mode):
                subprocess.run(['bash', str(ROOT / 'codex-switch.sh'), mode], env=env,
                               check=True, capture_output=True, text=True)
            switch('openai')
            switch('deepseek')
            self.assertEqual(json.loads(rollout.read_text())['model'], 'deepseek-flash')
            with sqlite3.connect(home / 'state_5.sqlite') as db:
                self.assertEqual(db.execute('select model, reasoning_effort from threads').fetchone(),
                                 ('deepseek-flash', 'high'))
            self.assertIn('gpt-5.6-terra', (home / 'config.toml.bak-switch').read_text())
            switch('openai')
            self.assertEqual(json.loads(rollout.read_text())['model'], 'gpt-5.6-terra')
            switch('deepseek')
            self.assertEqual(json.loads(rollout.read_text())['model'], 'deepseek-flash')


if __name__ == '__main__':
    unittest.main()
