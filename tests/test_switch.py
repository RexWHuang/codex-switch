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
            self.assertEqual(records()[0]['payload']['model'], 'deepseek-flash')
            self.assertEqual(records()[1], message)
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


if __name__ == '__main__':
    unittest.main()
