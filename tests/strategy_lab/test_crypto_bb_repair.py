from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app.strategy_lab.crypto_bb_repair import apply


class BBCodeRepair(unittest.TestCase):
    def test_code_only_update_preserves_wallet_config_and_other_workers(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);fleet=root/'fleet';work=fleet/'bb';shadow=root/'shadow';source=root/'source'
            for path in (work/'strategies',shadow/'strategies',source/'app/strategy_lab'):path.mkdir(parents=True)
            (source/'app/strategy_lab/crypto_fleet_runtime.py').write_text('# verified checkout')
            (work/'strategies/bb.py').write_text('old');(shadow/'strategies/bb.py').write_text('fixed')
            for path in (work,shadow):
                (path/'profile.json').write_text('{}');(path/'config.json').write_text('immutable config')
            (work/'paper.sqlite3').write_bytes(b'wallet must not change')
            (work/'PAUSE_NEW_ENTRIES').touch()
            compose={'services':{'bb':{'volumes':[{'target':'/check','source':'old','read_only':True}]},'e0':{'untouched':True}}}
            (fleet/'compose.json').write_text(json.dumps(compose))
            profile=dict(key='bb',generated_files={'bb.py':'hash'})
            with patch('app.strategy_lab.crypto_bb_repair.verify',return_value=({},profile)):
                result=apply(fleet,shadow,source)
            self.assertFalse(result['ledger_modified'])
            self.assertEqual((work/'paper.sqlite3').read_bytes(),b'wallet must not change')
            self.assertEqual((work/'config.json').read_text(),'immutable config')
            actual=json.loads((fleet/'compose.json').read_text())
            self.assertEqual(actual['services']['e0'],compose['services']['e0'])
            self.assertEqual((work/'strategies/bb.py').read_text(),'fixed')
            self.assertEqual((Path(result['backup'])/'strategies/bb.py').read_text(),'old')
            self.assertFalse(list(Path(result['backup']).glob('*.sqlite3')))

class DockerLogCapture(unittest.TestCase):
    def test_successful_native_stderr_is_captured_without_becoming_an_error(self):
        import subprocess, sys
        from app.strategy_lab.crypto_bb_repair import capture_docker_logs
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'native.log'
            def runner(args,**kwargs):
                self.assertEqual(args,['docker','logs','--tail','1000','bb-container'])
                return subprocess.run([sys.executable,'-c',
                    'import sys; print("heartbeat", file=sys.stderr); print("stdout")'],**kwargs)
            result=capture_docker_logs('bb-container',path,runner=runner)
            self.assertEqual(result['status'],'BB_LOGS_CAPTURED')
            self.assertIn('heartbeat',path.read_text())
            self.assertIn('stdout',path.read_text())

    def test_actual_native_failure_is_not_hidden_and_diagnostic_is_retained(self):
        import subprocess, sys
        from app.strategy_lab.crypto_bb_repair import capture_docker_logs
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'native.log'
            def runner(args,**kwargs):
                return subprocess.run([sys.executable,'-c',
                    'import sys; print("real failure", file=sys.stderr); sys.exit(1)'],**kwargs)
            with self.assertRaises(subprocess.CalledProcessError):
                capture_docker_logs('bb-container',path,runner=runner)
            self.assertIn('real failure',path.read_text())
