import json
import tempfile
import unittest
from contextlib import chdir
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import aggregate_watch

class AggregateRecovery(unittest.TestCase):
    def check_status(self,status,expected):
        with tempfile.TemporaryDirectory() as root,chdir(root):
            Path('daily.py').touch()
            def rebuild(*args,**kwargs):
                Path('reports').mkdir(exist_ok=True)
                Path('reports/2026-11-19.json').write_text(json.dumps({'date':'2026-11-19','status':status}))
                return SimpleNamespace(returncode=0)
            with patch('sys.argv',['aggregate_watch.py','--date','2026-11-19']), \
                 patch.object(aggregate_watch,'signatures',return_value=('new-source',)), \
                 patch.object(aggregate_watch.subprocess,'run',side_effect=rebuild):
                self.assertEqual(aggregate_watch.main(),expected)
    def test_partial_report_is_not_success(self):self.check_status('partial',2)
    def test_all_sources_complete_is_success(self):self.check_status('complete',0)

if __name__=='__main__':unittest.main()
