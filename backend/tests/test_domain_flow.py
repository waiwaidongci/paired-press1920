"""HTTP 级流程验算：用 SQLite + fakeredis 替代外部服务。

这些依赖仅在本地测试安装（pytest/httpx/fakeredis），不在生产 requirements.txt。
缺少这些包时整个模块跳过，核心算法验算仍由 test_algorithm.py 覆盖。
"""

import os
import tempfile
import unittest

try:
    import fakeredis
    from fastapi.testclient import TestClient
except ImportError:  # 生产镜像不装测试依赖
    fakeredis = None
    TestClient = None


@unittest.skipIf(TestClient is None, '需要 httpx 与 fakeredis 才能运行 HTTP 验算')
class DomainFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.mkdtemp()
        os.environ.setdefault('DATABASE_URL', 'sqlite:///' + tmp + '/test.db')
        os.environ.setdefault('REDIS_URL', 'redis://localhost:1/0')
        os.environ.setdefault('JWT_SECRET', 'test-secret')
        os.environ.setdefault('SEED_DEMO', 'false')
        cls.fake = fakeredis.FakeRedis(decode_responses=True)
        import src.middlewares.rate_limit as rate_limit
        rate_limit.redis = cls.fake
        import src.services.runs as runs_service
        runs_service._redis = lambda: cls.fake
        from src.main import app
        from src.config.database import Base, engine, Session
        from src.models.user import User
        from src.utils.security import hash_password
        Base.metadata.create_all(engine)
        with Session() as db:
            for role in ('admin', 'analyst', 'viewer'):
                db.add(User(email=role + '@flow.local', role=role, password_hash=hash_password('pw')))
            db.commit()
        cls.client = TestClient(app)
        cls.token = {
            role: {'Authorization': 'Bearer ' + cls.client.post(
                '/auth/login', json={'email': role + '@flow.local', 'password': 'pw'}
            ).json()['token']}
            for role in ('admin', 'analyst', 'viewer')
        }

    def setUp(self):
        self.fake.delete('analysis-queue')

    def study(self, name='流程批次'):
        r = self.client.post('/studies', json={'name': name, 'source_filename': 'a.csv'}, headers=self.token['analyst'])
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['id']

    def add(self, sid, source, a, b, low, high, headers=None):
        return self.client.post(
            f'/studies/{sid}/reports',
            json={'source_key': source, 'process_a': a, 'process_b': b, 'low': low, 'high': high},
            headers=headers or self.token['analyst'],
        )

    def queue_run(self, sid, name='核算'):
        return self.client.post(
            '/runs',
            json={'study_id': sid, 'name': name, 'target': {'process_from': 'A', 'process_to': 'B'}, 'parameters': {}},
            headers=self.token['analyst'],
        )

    def test_invalid_input_never_queues(self):
        sid = self.study()
        for payload in (
            ('', 'A', 'B', '0', '1'),
            ('S', 'A', 'A', '0', '1'),
            ('S', 'A', 'B', '5', '1'),
            ('S', 'A', 'B', 'nan', '1'),
            ('S', 'A', 'B', 'inf', '1'),
        ):
            r = self.add(sid, *payload)
            self.assertEqual(r.status_code, 422, payload)
        self.assertEqual(self.fake.llen('analysis-queue'), 0)
        empty = self.study('空批次')
        self.assertEqual(self.queue_run(empty).status_code, 422)

    def test_over_twenty_reports_rejected(self):
        sid = self.study('满载批次')
        pairs = [('A', 'B'), ('B', 'C'), ('A', 'C')]
        for i in range(21):
            a, b = pairs[i % 3]
            self.assertEqual(self.add(sid, f'S{i}', a, b, '0', '1').status_code, 200)
        r = self.queue_run(sid)
        self.assertEqual(r.status_code, 422)
        self.assertIn('20', r.json()['detail'])
        self.assertEqual(self.fake.llen('analysis-queue'), 0)

    def test_frozen_snapshot_and_revision(self):
        sid = self.study('冻结批次')
        row = self.add(sid, 'R1', 'A', 'B', '0', '1').json()
        self.assertEqual(row['revision'], 1)
        queued = self.queue_run(sid)
        self.assertEqual(queued.status_code, 200)
        frozen = queued.json()['snapshot']
        self.assertEqual(frozen['reports'][0]['low'], '0')
        updated = self.client.put(
            f'/studies/{sid}/reports/{row["id"]}',
            json={'source_key': 'R1', 'process_a': 'A', 'process_b': 'B', 'low': '9', 'high': '9', 'expected_revision': 1},
            headers=self.token['analyst'],
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()['revision'], 2)
        stale = self.client.put(
            f'/studies/{sid}/reports/{row["id"]}',
            json={'source_key': 'R1', 'process_a': 'A', 'process_b': 'B', 'low': '8', 'high': '8', 'expected_revision': 1},
            headers=self.token['analyst'],
        )
        self.assertEqual(stale.status_code, 409)
        # 排队中的任务保留各自冻结的数据与目标。
        again = self.client.get(f"/runs/{queued.json()['id']}", headers=self.token['viewer']).json()
        self.assertEqual(again['snapshot'], frozen)
        # 新任务拿到新版数据，旧结果不受影响。
        second = self.queue_run(sid, '核算二').json()
        self.assertEqual(second['snapshot']['reports'][0]['low'], '9')
        self.assertEqual(second['snapshot']['reports'][0]['revision'], 2)

    def test_worker_result_and_export_permissions(self):
        sid = self.study('导出批次')
        for row in (('R1', 'A', 'B', '0', '1'), ('R2', 'B', 'C', '1', '1'), ('R3', 'C', 'A', '1', '1')):
            self.assertEqual(self.add(sid, *row).status_code, 200)
        queued = self.queue_run(sid).json()
        from src.config.database import Session
        from src.models.run import Run
        from src.services.algorithm import calculate
        with Session() as db:
            run = db.get(Run, queued['id'])
            run.result = calculate(run.snapshot, run.parameters)
            run.status = 'completed'
            db.commit()
        got = self.client.get(f"/runs/{queued['id']}", headers=self.token['viewer']).json()
        self.assertEqual(got['result']['metrics']['最少剔除报告数'], 1)
        self.assertEqual(len(got['result']['lower_witnesses']), 1)
        self.assertEqual(self.client.get(f"/runs/{queued['id']}/export", headers=self.token['viewer']).status_code, 403)
        self.assertEqual(self.client.post(
            f"/runs/{queued['id']}/review", json={'note': '范围与剔除理由已确认'}, headers=self.token['admin']
        ).status_code, 200)
        exported = self.client.get(f"/runs/{queued['id']}/export", headers=self.token['viewer'])
        self.assertEqual(exported.status_code, 200)
        self.assertIn('覆盖全部方案的下界', exported.text)


if __name__ == '__main__':
    unittest.main()
