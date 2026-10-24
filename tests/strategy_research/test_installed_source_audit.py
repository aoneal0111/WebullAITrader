import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from app.strategy_research.installed_source_audit import FILES,compare_sources,fingerprint


class InstalledSourceAuditTests(unittest.TestCase):
    def test_fixed_files_compare_lf_crlf_and_do_not_export_contents(self):
        with tempfile.TemporaryDirectory() as root:
            a,b=Path(root)/'installed',Path(root)/'review'
            for directory,raw in ((a,b'print("fixture")\r\n'),(b,b'print("fixture")\n')):
                for name in FILES:
                    p=directory/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
            (a/'.env').write_text('SECRET=not-exported')
            report=compare_sources(a,b)
            self.assertTrue(report['all_files_match_review'])
            self.assertFalse(report['runtime_loaded_version_verified'])
            self.assertNotIn('fixture',json.dumps(report))
            self.assertNotIn('SECRET',json.dumps(report))
            (a/FILES[0]).write_text('changed')
            self.assertEqual(compare_sources(a,b)['files'][0]['comparison'],'DIFFERENT')
            (a/FILES[0]).unlink()
            self.assertEqual(compare_sources(a,b)['files'][0]['installed']['status'],'MISSING')

    def test_outside_root_and_byte_limits_do_not_read_extra_files(self):
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)/'inside';folder.mkdir()
            external=Path(root)/'external';external.write_text('secret')
            self.assertEqual(fingerprint(folder,'../external'),{'status':'OUTSIDE_ROOT'})
            big=folder/'big';big.write_bytes(b'x'*(2*1024*1024+1))
            self.assertEqual(fingerprint(folder,'big'),{'status':'SIZE_LIMIT'})

    def test_cli_has_no_site_package_dependencies(self):
        with tempfile.TemporaryDirectory() as root:
            result=subprocess.run([sys.executable,'-S','-m','app.strategy_research.installed_source_audit',
                '--installed-root',root,'--review-root',root],capture_output=True,text=True,
                cwd=Path(__file__).resolve().parents[2],timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertFalse(json.loads(result.stdout)['all_files_match_review'])


if __name__=='__main__':unittest.main()
