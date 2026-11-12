import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CollectCliTests(unittest.TestCase):
    def run_cli(self, *arguments):
        return subprocess.run(
            [sys.executable, 'app.py', *arguments],
            capture_output=True, text=True, timeout=120)

    def test_collect_completes_and_progress_reports_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / 'raw.sqlite3')
            completed = self.run_cli('collect', '--output', output, '--job-id', 'cli-1')
            self.assertEqual(completed.returncode, 0, completed.stderr)
            data = json.loads(completed.stdout)
            self.assertEqual(data['status'], 'completed')
            self.assertEqual(len(data['catalog']), 17)

            progress = self.run_cli('progress', '--output', output, '--job-id', 'cli-1')
            self.assertEqual(progress.returncode, 0, progress.stderr)
            report = json.loads(progress.stdout)
            self.assertEqual(report['publication_count'], 17)
            self.assertEqual(report['status'], 'completed')

            listing = self.run_cli('progress', '--output', output)
            self.assertEqual(listing.returncode, 0, listing.stderr)
            self.assertEqual(len(json.loads(listing.stdout)['jobs']), 1)

    def test_demo_single_screen_capability_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / 'raw.sqlite3')
            process = self.run_cli('demo', '--output', output)
            self.assertEqual(process.returncode, 0, process.stderr)
            data = json.loads(process.stdout)
            self.assertEqual(data['可见行数'], 5)
            self.assertEqual(data['声明总行数'], 17)

    def test_interrupt_returns_structured_reason_and_resume_finishes(self):
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / 'raw.sqlite3')
            interrupted = self.run_cli(
                'collect', '--output', output, '--job-id', 'cli-2', '--stop-after', '3')
            self.assertEqual(interrupted.returncode, 3, interrupted.stderr)
            payload = json.loads(interrupted.stdout)
            self.assertTrue(payload['interrupted'])
            self.assertEqual(payload['steps_completed'], 3)

            resumed = self.run_cli('collect', '--output', output, '--job-id', 'cli-2')
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            data = json.loads(resumed.stdout)
            self.assertEqual(data['status'], 'completed')
            self.assertEqual(data['published_skipped'], 3)
            self.assertEqual(data['publication_count'], 17)

    def test_suspended_job_returns_nonzero_exit_code(self):
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / 'raw.sqlite3')
            suspended = self.run_cli(
                'collect', '--output', output, '--job-id', 'cli-3',
                '--mutate', '--max-attempts', '1')
            self.assertEqual(suspended.returncode, 2, suspended.stderr)
            payload = json.loads(suspended.stdout)
            self.assertTrue(payload['suspended'])
            self.assertTrue(payload['suspension_reason'] == 'retry-budget-exhausted')
            self.assertIn('不与其他代际拼接', payload['suspension_detail'])

    def test_progress_unknown_job_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / 'raw.sqlite3')
            process = self.run_cli('progress', '--output', output, '--job-id', 'missing')
            self.assertEqual(process.returncode, 1)


if __name__ == '__main__':
    unittest.main()
