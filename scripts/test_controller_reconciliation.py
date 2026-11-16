from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speech_controller.file_lock import exclusive_lock
from speech_controller.journal import DurableLifecycleExecutor, JournalUnavailable
from speech_controller.lifecycle import SwitchRequest
from test_controller_lifecycle import Backend, DEPLOYMENTS, ROUTES


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'controller.sqlite3'
        self.backend = Backend()
        self.backend.verify_reconciliation = Mock(return_value=True)
        self.executor = self.reopen()

    def reopen(self):
        return DurableLifecycleExecutor(self.backend, DEPLOYMENTS, ROUTES, self.path)

    def interrupt(self):
        class Crash(BaseException):
            pass
        request = SwitchRequest('switch', 0, ('en', 'stt'), 'new-stt')
        with patch.object(self.backend, 'start', side_effect=Crash), self.assertRaises(Crash):
            self.executor.execute(request)
        return request

    def recover(self, **changes):
        args = dict(recovery_id='recovery', expected_revision=0, routes=ROUTES,
                    actor='fixture-operator', reason='Inspected routing and worker cleanup')
        return self.executor.reconcile(**(args | changes))

    def test_restart_reconcile_audit_and_idempotency(self):
        request = self.interrupt()
        self.executor = self.reopen()
        result = self.recover()
        self.assertEqual((result.status, result.state.revision), ('reconciled', 1))
        self.backend.verify_reconciliation.assert_called_once()
        self.assertEqual(self.recover(), result)
        self.assertEqual(self.recover(reason='different').reason, 'recovery_id_conflict')
        restarted = self.reopen()
        calls = list(self.backend.calls)
        self.assertEqual(restarted.execute(request), result)
        self.assertFalse(restarted.recovery_status()['blocked'])
        self.assertEqual(self.backend.calls, calls)
        history = restarted.reconciliation_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['request']['actor'], 'fixture-operator')
        self.assertEqual(history[0]['pending_requests'], ('switch',))
        self.assertEqual(history[0]['prior_state'].revision, 0)

    def test_verifier_denial_exception_stale_and_invalid_routes_never_reset(self):
        self.assertEqual(self.recover().reason, 'reconciliation_not_required')
        self.interrupt()
        self.assertEqual(self.recover(expected_revision=1).reason, 'stale_revision')
        with self.assertRaises(ValueError):
            self.recover(routes=ROUTES | {('en', 'stt'): 'shared-tts'})
        for verdict in (False, 1, RuntimeError('secret backend error')):
            self.backend.verify_reconciliation.side_effect = verdict if isinstance(verdict, Exception) else None
            self.backend.verify_reconciliation.return_value = verdict
            result = self.recover()
            self.assertEqual(result.reason, 'reconciliation_unverified')
            self.assertNotIn('secret', str(result))
            self.assertTrue(self.executor.recovery_status()['blocked'])
        self.assertEqual(self.executor.reconciliation_history(), ())

    def test_final_audit_write_failure_rolls_back_everything(self):
        self.interrupt()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON reconciliations BEGIN SELECT RAISE(ABORT, 'secret'); END")
            db.commit()
        with self.assertRaises(JournalUnavailable):
            self.recover()
        restarted = self.reopen()
        self.assertEqual(restarted.state.revision, 0)
        self.assertEqual(restarted.recovery_status()['pending_requests'], ('switch',))
        self.assertEqual(restarted.reconciliation_history(), ())

    def test_completed_failure_preserved_and_audit_limit_blocks(self):
        self.backend.not_ready.add('new-stt')
        self.backend.fail.add(('stop', 'new-stt'))
        request = SwitchRequest('failed', 0, ('en', 'stt'), 'new-stt')
        failure = self.executor.execute(request)
        self.assertEqual(failure.status, 'recovery_required')
        self.assertEqual(self.recover().status, 'reconciled')
        self.assertEqual(self.reopen().execute(request), failure)
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute('SELECT request,outcome,recorded_at,prior_state,pending_requests FROM reconciliations').fetchone()
            db.executemany('INSERT INTO reconciliations VALUES (?,?,?,?,?,?)',
                           [(f'fixture-{i}', *row) for i in range(127)])
            db.execute('UPDATE metadata SET blocked=1')
            db.commit()
        count = self.backend.verify_reconciliation.call_count
        result = self.recover(recovery_id='full', expected_revision=1)
        self.assertEqual(result.reason, 'reconciliation_history_full')
        self.assertEqual(self.backend.verify_reconciliation.call_count, count)

    def test_verification_holds_lock_against_execution(self):
        self.interrupt()
        entered, release = Event(), Event()
        def verify(*args):
            entered.set()
            release.wait(3)
            return True
        other = self.reopen()
        self.backend.verify_reconciliation.side_effect = verify
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.recover)
            self.assertTrue(entered.wait(1))
            try:
                result = other.execute(SwitchRequest('other', 0, ('en', 'stt'), 'new-stt'))
                self.assertEqual(result.reason, 'busy')
            finally:
                release.set()
            self.assertEqual(future.result().status, 'reconciled')

    def test_active_switch_excludes_reconciliation(self):
        entered, release = Event(), Event()
        original = self.backend.revalidate
        def wait(current, proposed):
            entered.set()
            release.wait(3)
            return original(current, proposed)
        other = self.reopen()
        with patch.object(self.backend, 'revalidate', side_effect=wait), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.executor.execute, SwitchRequest('switch', 0, ('en', 'stt'), 'new-stt'))
            self.assertTrue(entered.wait(1))
            try:
                result = other.reconcile('recover', 0, ROUTES, 'operator', 'checked')
                self.assertEqual(result.reason, 'busy')
                self.backend.verify_reconciliation.assert_not_called()
            finally:
                release.set()
            self.assertEqual(future.result().status, 'succeeded')

    def test_real_process_lock_and_legacy_table_upgrade(self):
        lock_path = str(self.path) + '.lock'
        code = "from speech_controller.file_lock import exclusive_lock; import sys;\nwith exclusive_lock(sys.argv[1]) as held: print('locked' if held else 'busy')"
        with exclusive_lock(lock_path) as held:
            self.assertTrue(held)
            probe = subprocess.run([sys.executable, '-c', code, lock_path], capture_output=True, text=True, check=True)
            self.assertEqual(probe.stdout.strip(), 'busy')
        probe = subprocess.run([sys.executable, '-c', code, lock_path], capture_output=True, text=True, check=True)
        self.assertEqual(probe.stdout.strip(), 'locked')
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('DROP TABLE reconciliations')
            db.commit()
        self.assertEqual(self.reopen().reconciliation_history(), ())


if __name__ == '__main__':
    unittest.main()
