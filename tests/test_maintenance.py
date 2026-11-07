import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('weekly_reboot',Path(__file__).resolve().parents[1]/'system/weekly-reboot.py')
guard=importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class MaintenanceTests(unittest.TestCase):
    def test_missing_and_unlocked_files_do_not_block(self):
        with tempfile.TemporaryDirectory() as folder:
            lock=Path(folder)/'lock'; lock.touch()
            self.assertEqual(guard.pending_locks([lock,Path(folder)/'missing']),[])
            self.assertEqual(guard.pending_locks([lock]),[])

    def test_actual_posix_lock_is_detected_and_then_released(self):
        with tempfile.TemporaryDirectory() as folder:
            lock=Path(folder)/'lock'; lock.touch()
            code="import fcntl,sys; f=open(sys.argv[1],'r+'); fcntl.lockf(f,fcntl.LOCK_EX); print('locked',flush=True); sys.stdin.read()"
            child=subprocess.Popen([sys.executable,'-c',code,str(lock)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(),'locked')
                self.assertEqual(guard.pending_locks([lock]),[str(lock)])
            finally:
                child.communicate(timeout=5)
            self.assertEqual(guard.pending_locks([lock]),[])

    def test_check_only_never_requests_reboot(self):
        with patch.object(guard,'pending_locks',return_value=[]),patch.object(guard.subprocess,'run') as run:
            guard.main(['--check-only'])
            run.assert_not_called()

    def test_busy_operation_delays_request_until_released(self):
        with patch.object(guard,'pending_locks',side_effect=[['package-lock'],[]]),patch.object(guard.time,'sleep') as sleep,patch.object(guard.subprocess,'run') as run:
            guard.main([])
            sleep.assert_called_once_with(30)
            run.assert_called_once_with(['/usr/bin/systemctl','--no-block','reboot'],check=True)

    def test_busy_operation_past_deadline_prevents_reboot(self):
        with patch.object(guard,'pending_locks',return_value=['package-lock']),patch.object(guard.time,'monotonic',side_effect=[0,3601]),patch.object(guard.subprocess,'run') as run:
            with self.assertRaises(SystemExit):
                guard.main([])
            run.assert_not_called()


if __name__=='__main__':unittest.main()
