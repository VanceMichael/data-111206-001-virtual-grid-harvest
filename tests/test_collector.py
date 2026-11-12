import tempfile
import unittest
from pathlib import Path

from portal import serve
from raw_store import RawStore
from progress_store import ProgressStore
from collector import collect_catalog, CollectionInterrupted, CatalogCollectionSuspended


class CollectorHarness(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.raw = RawStore(root / 'raw.sqlite3')
        self.progress = ProgressStore(root / 'progress.sqlite3')

    def tearDown(self):
        self.raw.close()
        self.progress.close()
        self.directory.cleanup()

    def collect(self, url, **kwargs):
        return collect_catalog(url, self.progress, self.raw, **kwargs)


class FullCatalogTests(CollectorHarness):
    def test_complete_catalog_covers_every_logical_row_once(self):
        with serve() as base:
            result = self.collect(base, job_id='job-full')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['final_revision'], '1')
        self.assertEqual(result['declared_total'], 17)
        self.assertEqual(len(result['catalog']), 17)
        # 逻辑行 0..16 全覆盖、不缺不重
        self.assertEqual([row['row_index'] for row in result['catalog']], list(range(17)))
        references = [row['reference'] for row in result['catalog']]
        self.assertEqual(len(set(references)), 17)
        # 固定分组行不是展商行，不会被计入
        self.assertNotIn('固定分组', [row['name'] for row in result['catalog']])
        evidence = result['end_evidence']
        self.assertTrue(evidence['at_bottom'])
        self.assertTrue(evidence['tail_stable'])
        self.assertTrue(evidence['revision_unchanged'])
        self.assertEqual(evidence['missing_logical_rows'], [])
        # 每条详情都作为原文落库
        self.assertEqual(result['publication_count'], 17)
        for publication in result['publications']:
            body = self.raw.read(publication['digest'])
            self.assertIn(publication['reference'].encode(), body)

    def test_same_name_companies_are_distinct_subjects(self):
        with serve() as base:
            result = self.collect(base, job_id='job-name')
        same_name = result['same_name_subjects']
        self.assertEqual(same_name, {'合成同名企业': 2})
        rows = [row for row in result['catalog'] if row['name'] == '合成同名企业']
        self.assertEqual({row['reference'] for row in rows}, {'EXH-003', 'EXH-009'})
        self.assertEqual({row['booth'] for row in rows}, {'A-03', 'A-09'})

    def test_row_text_does_not_include_detail_button_label(self):
        with serve() as base:
            result = self.collect(base, job_id='job-text')
        self.assertTrue(all('详情' not in row['booth'] for row in result['catalog']))


class MutationTests(CollectorHarness):
    def test_insertion_aborts_old_revision_and_recollects_without_splicing(self):
        with serve() as base:
            result = self.collect(base + '?mutate=1', job_id='job-mut')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['final_revision'], '2')
        self.assertEqual(result['declared_total'], 18)
        self.assertEqual(len(result['catalog']), 18)
        self.assertIn('EXH-NEW', {row['reference'] for row in result['catalog']})
        attempts = result['attempts']
        self.assertEqual(attempts[0]['status'], 'aborted')
        self.assertEqual(attempts[0]['reason'], 'revision-changed')
        self.assertEqual(attempts[1]['status'], 'completed')
        self.assertEqual(attempts[1]['revision'], '2')
        # 代际 1 的部分观察保留可审计，但最终名单只来自代际 2
        rev1_rows = self.progress.get_rows('job-mut', '1')
        self.assertTrue(rev1_rows)
        self.assertEqual(len(self.progress.get_rows('job-mut', '2')), 18)

    def test_retry_budget_suspends_without_claiming_completeness(self):
        with serve() as base:
            result = self.collect(base + '?mutate=1', job_id='job-susp', max_attempts=1)
        self.assertTrue(result['suspended'])
        self.assertEqual(result['suspension_reason'], 'retry-budget-exhausted')
        self.assertEqual(result['status'], 'suspended')
        job = self.progress.get_job('job-susp')
        self.assertEqual(job['status'], 'suspended')
        self.assertIn('retry-budget-exhausted', job['reason'])
        # 挂起作业不能被当作完整结果再次发布
        with self.assertRaises(CatalogCollectionSuspended):
            self.collect(base + '?mutate=1', job_id='job-susp')


class RestartTests(CollectorHarness):
    def test_resume_same_revision_skips_published_details(self):
        with serve() as base:
            with self.assertRaises(CollectionInterrupted) as caught:
                self.collect(base, job_id='job-resume', stop_after=4)
            self.assertEqual(caught.exception.steps, 4)
            self.assertIn(0, caught.exception.covered)
            partial = self.progress.summary('job-resume')
            self.assertEqual(partial['status'], 'running')
            self.assertEqual(partial['publication_count'], 4)

            result = self.collect(base, job_id='job-resume')
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(result['resumed'])
        self.assertEqual(result['published_skipped'], 4)
        self.assertEqual(result['published_new'], 13)
        # 台账仍只有 17 条，恢复后没有重复发布
        self.assertEqual(result['publication_count'], 17)
        references = [p['reference'] for p in result['publications']]
        self.assertEqual(len(set(references)), 17)

    def test_resume_revalidates_revision_and_does_not_splice_generations(self):
        with serve() as base:
            with self.assertRaises(CollectionInterrupted):
                self.collect(base, job_id='job-reval', stop_after=3)
            # 浏览器关闭期间页面代际被推进（模拟现场目录变更）
            import json
            from urllib.request import urlopen
            with urlopen(base + '/advance') as response:
                advanced = json.load(response)
            self.assertEqual(advanced['revision'], 2)

            result = self.collect(base, job_id='job-reval')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['final_revision'], '2')
        attempts = result['attempts']
        self.assertEqual(attempts[0]['status'], 'abandoned')
        self.assertEqual(attempts[0]['reason'], 'resume-revision-mismatch')
        self.assertEqual(attempts[1]['revision'], '2')
        self.assertEqual(attempts[1]['status'], 'completed')
        # 旧代际已发布详情不重复发布，新代际按当前页面重新核验
        self.assertGreaterEqual(result['published_skipped'], 1)
        self.assertEqual(len(result['catalog']), 18)


if __name__ == '__main__':
    unittest.main()
