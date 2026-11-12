"""采集进度持久化。

与 RawStore 分工：RawStore 只存原始观察字节，不表达业务完整性；
本模块记录采集作业的可解释状态——页面代际、每次尝试覆盖的逻辑行、
按 reference 去重的发布台账，保证浏览器关闭后可以续采且已发布详情不重复。
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class ProgressStore:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.execute('PRAGMA foreign_keys=ON')
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                max_attempts INTEGER NOT NULL,
                status TEXT NOT NULL,
                reason TEXT,
                final_revision TEXT,
                declared_total INTEGER,
                attempts_used INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts (
                job_id TEXT NOT NULL,
                attempt_no INTEGER NOT NULL,
                revision TEXT NOT NULL,
                declared_total INTEGER,
                covered TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(job_id, attempt_no));
            CREATE TABLE IF NOT EXISTS row_observations (
                job_id TEXT NOT NULL,
                revision TEXT NOT NULL,
                row_index INTEGER NOT NULL,
                reference TEXT NOT NULL,
                name TEXT,
                country TEXT,
                booth TEXT,
                observed_at TEXT NOT NULL,
                PRIMARY KEY(job_id, revision, row_index));
            CREATE TABLE IF NOT EXISTS publications (
                job_id TEXT NOT NULL,
                reference TEXT NOT NULL,
                revision TEXT NOT NULL,
                row_index INTEGER,
                digest TEXT,
                published_at TEXT NOT NULL,
                PRIMARY KEY(job_id, reference));
        """)

    # ---- 作业 ----
    def create_job(self, job_id, url, max_attempts):
        now = utc_now()
        with self.connection:
            self.connection.execute(
                'INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)',
                (job_id, url, max_attempts, 'running', None, None, None, 0, now, now))
        return self.get_job(job_id)

    def get_job(self, job_id):
        row = self.connection.execute('SELECT * FROM jobs WHERE job_id=?', (job_id,)).fetchone()
        return dict(row) if row else None

    def update_job(self, job_id, status=None, reason=None, final_revision=None,
                   declared_total=None, attempts_used=None):
        current = self.get_job(job_id)
        if current is None:
            raise KeyError(job_id)
        with self.connection:
            self.connection.execute("""
                UPDATE jobs SET status=COALESCE(?,status), reason=COALESCE(?,reason),
                    final_revision=COALESCE(?,final_revision),
                    declared_total=COALESCE(?,declared_total),
                    attempts_used=COALESCE(?,attempts_used), updated_at=?
                WHERE job_id=?""",
                (status, reason, final_revision, declared_total, attempts_used, utc_now(), job_id))
        return self.get_job(job_id)

    def list_jobs(self):
        rows = self.connection.execute('SELECT * FROM jobs ORDER BY created_at').fetchall()
        return [dict(row) for row in rows]

    # ---- 尝试（按页面代际隔离，不跨代际拼接）----
    def save_attempt(self, job_id, attempt_no, revision, declared_total, covered, status, reason=None):
        now = utc_now()
        with self.connection:
            self.connection.execute("""
                INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(job_id,attempt_no) DO UPDATE SET
                    revision=excluded.revision,
                    declared_total=excluded.declared_total,
                    covered=excluded.covered,
                    status=excluded.status,
                    reason=excluded.reason,
                    updated_at=excluded.updated_at""",
                (job_id, attempt_no, str(revision), declared_total,
                 json.dumps(sorted(covered)), status, reason, now))
            used = self.connection.execute(
                'SELECT COUNT(*) FROM attempts WHERE job_id=?', (job_id,)).fetchone()[0]
            self.connection.execute(
                'UPDATE jobs SET attempts_used=?, updated_at=? WHERE job_id=?', (used, now, job_id))

    def get_attempt(self, job_id, attempt_no):
        row = self.connection.execute(
            'SELECT * FROM attempts WHERE job_id=? AND attempt_no=?', (job_id, attempt_no)).fetchone()
        if row is None:
            return None
        data = dict(row)
        data['covered'] = json.loads(data['covered'])
        return data

    def latest_attempt(self, job_id):
        row = self.connection.execute(
            'SELECT * FROM attempts WHERE job_id=? ORDER BY attempt_no DESC LIMIT 1', (job_id,)).fetchone()
        if row is None:
            return None
        data = dict(row)
        data['covered'] = json.loads(data['covered'])
        return data

    def list_attempts(self, job_id):
        rows = self.connection.execute(
            'SELECT * FROM attempts WHERE job_id=? ORDER BY attempt_no', (job_id,)).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data['covered'] = json.loads(data['covered'])
            result.append(data)
        return result

    # ---- 目录行观察（同一作业、同一代际、同一逻辑行幂等覆盖）----
    def save_row(self, job_id, revision, row_index, reference, name, country, booth, observed_at):
        with self.connection:
            self.connection.execute("""
                INSERT INTO row_observations VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(job_id,revision,row_index) DO UPDATE SET
                    reference=excluded.reference, name=excluded.name,
                    country=excluded.country, booth=excluded.booth,
                    observed_at=excluded.observed_at""",
                (job_id, str(revision), row_index, reference, name, country, booth, observed_at))

    def get_rows(self, job_id, revision):
        rows = self.connection.execute("""
            SELECT row_index,reference,name,country,booth,observed_at
            FROM row_observations WHERE job_id=? AND revision=? ORDER BY row_index""",
            (job_id, str(revision))).fetchall()
        return [dict(row) for row in rows]

    # ---- 发布台账：恢复时已发布详情不重复发布 ----
    def is_published(self, job_id, reference):
        return self.connection.execute(
            'SELECT 1 FROM publications WHERE job_id=? AND reference=?',
            (job_id, reference)).fetchone() is not None

    def add_publication(self, job_id, reference, revision, row_index, digest, published_at):
        """返回 True 表示新发布，False 表示台账中已存在（不重复发布）。"""
        with self.connection:
            cursor = self.connection.execute(
                'INSERT OR IGNORE INTO publications VALUES(?,?,?,?,?,?)',
                (job_id, reference, str(revision), row_index, digest, published_at))
            return cursor.rowcount == 1

    def publications(self, job_id):
        rows = self.connection.execute(
            'SELECT * FROM publications WHERE job_id=? ORDER BY row_index,reference', (job_id,)).fetchall()
        return [dict(row) for row in rows]

    def summary(self, job_id):
        job = self.get_job(job_id)
        if job is None:
            return None
        attempts = self.list_attempts(job_id)
        publications = self.publications(job_id)
        revision = job['final_revision'] or (attempts[-1]['revision'] if attempts else None)
        rows = self.get_rows(job_id, revision) if revision is not None else []
        return {
            'job_id': job_id,
            'url': job['url'],
            'status': job['status'],
            'reason': job['reason'],
            'max_attempts': job['max_attempts'],
            'attempts_used': job['attempts_used'],
            'final_revision': revision,
            'declared_total': job['declared_total'],
            'attempts': attempts,
            'rows_in_final_revision': rows,
            'publication_count': len(publications),
            'publications': publications,
            'created_at': job['created_at'],
            'updated_at': job['updated_at'],
        }

    def close(self):
        self.connection.close()
