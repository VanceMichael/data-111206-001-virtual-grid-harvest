import tempfile
import unittest
from pathlib import Path

from progress_store import ProgressStore


class ProgressStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'progress.sqlite3'
        self.store = ProgressStore(self.path)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_job_lifecycle(self):
        self.store.create_job('job-1', 'http://x', 3)
        job = self.store.get_job('job-1')
        self.assertEqual(job['status'], 'running')
        self.assertEqual(job['max_attempts'], 3)
        self.store.update_job('job-1', status='completed', final_revision='2',
                              declared_total=18)
        self.assertEqual(self.store.get_job('job-1')['status'], 'completed')

    def test_attempts_are_revision_isolated_and_idempotent(self):
        self.store.create_job('job-1', 'http://x', 3)
        self.store.save_attempt('job-1', 1, '1', 17, [0, 1], 'running')
        self.store.save_attempt('job-1', 1, '1', 17, [0, 1, 2], 'running')
        self.assertEqual(self.store.latest_attempt('job-1')['covered'], [0, 1, 2])
        self.store.save_attempt('job-1', 2, '2', 18, [0], 'running')
        attempts = self.store.list_attempts('job-1')
        self.assertEqual([a['revision'] for a in attempts], ['1', '2'])
        self.assertEqual(self.store.get_job('job-1')['attempts_used'], 2)

    def test_publication_ledger_dedupes_by_reference_not_name(self):
        self.store.create_job('job-1', 'http://x', 3)
        # 同名但不同 reference：两条都是独立主体，都要发布
        self.assertTrue(self.store.add_publication('job-1', 'EXH-003', '1', 2, 'd1', 't1'))
        self.assertTrue(self.store.add_publication('job-1', 'EXH-009', '1', 8, 'd2', 't2'))
        # 恢复后同一 reference 不重复发布
        self.assertFalse(self.store.add_publication('job-1', 'EXH-003', '1', 2, 'd1', 't3'))
        self.assertTrue(self.store.is_published('job-1', 'EXH-003'))
        self.assertEqual(len(self.store.publications('job-1')), 2)

    def test_row_observations_keyed_by_revision_and_logical_row(self):
        self.store.create_job('job-1', 'http://x', 3)
        self.store.save_row('job-1', '1', 0, 'EXH-001', '甲', '国', 'A-1', 't1')
        self.store.save_row('job-1', '2', 0, 'EXH-NEW', '新', '国', 'B-1', 't2')
        self.assertEqual(len(self.store.get_rows('job-1', '1')), 1)
        self.assertEqual(self.store.get_rows('job-1', '2')[0]['reference'], 'EXH-NEW')

    def test_summary_reopens_after_close(self):
        self.store.create_job('job-1', 'http://x', 3)
        self.store.save_attempt('job-1', 1, '1', 17, [0], 'completed')
        self.store.close()
        reopened = ProgressStore(self.path)
        summary = reopened.summary('job-1')
        self.assertEqual(summary['attempts_used'], 1)
        self.assertEqual(summary['final_revision'], '1')
        reopened.close()


if __name__ == '__main__':
    unittest.main()
