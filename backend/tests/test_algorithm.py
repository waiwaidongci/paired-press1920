"""差值区间推断的独立验算（纯标准库，无数据库）。

覆盖：
- 小样本穷举全部剔除方案，核对最少剔除数与上下界；
- “删不同报告会得出不同边界”的小样本；
- 最优方案中目标工艺对断开即无界；
- 输入校验失败；
- 并发改录冻结快照（调用后改源数据不影响快照）；
- 满载 20 份报告在 30 秒上限内完成。

运行：python3 -m unittest discover -s tests -v（在 backend/ 下）
"""

import itertools
import random
import time
import unittest

from src.services import algorithm as algo
from src.services.snapshots import snapshot_from_rows
from src.services.domain_validation import check_revision, validate_parameters, validate_report


def graph(reports, target_from, target_to):
    snap = {
        'reports': [
            {
                'id': i,
                'source': r[0],
                'process_a': r[1],
                'process_b': r[2],
                'low': r[3],
                'high': r[4],
            }
            for i, r in enumerate(reports)
        ],
        'target': {'from': target_from, 'to': target_to},
    }
    normalised, target = algo.normalize_snapshot(snap)
    names, index, edges, scale, digits = algo._build_graph(normalised)
    return snap, normalised, target, names, index, edges, scale


def brute_force(reports, target_from, target_to):
    """穷举所有剔除子集，返回 (最少剔除数, 全部最优方案, 覆盖上下界或 None)。"""
    snap, normalised, target, names, index, edges, scale = graph(reports, target_from, target_to)
    size = len(names)
    if target_from not in index or target_to not in index:
        return None
    a, b = index[target_from], index[target_to]
    report_count = len(reports)
    minimum = None
    optimal = []
    for size_removed in range(report_count + 1):
        for combo in itertools.combinations(range(report_count), size_removed):
            mask = sum(1 << i for i in combo)
            if algo._negative_cycle(size, edges, mask) is not None:
                continue
            if minimum is None:
                minimum = size_removed
            if size_removed == minimum:
                optimal.append(mask)
        if minimum is not None:
            break
    bounds = []
    for mask in optimal:
        dist_from = algo._shortest(size, edges, mask, a)
        dist_to = algo._shortest(size, edges, mask, b)
        if dist_from[b] is None or dist_to[a] is None:
            bounds.append(None)
        else:
            bounds.append((algo.Decimal(-dist_to[a]) / scale, algo.Decimal(dist_from[b]) / scale))
    if any(x is None for x in bounds):
        return ('unbounded', minimum, optimal)
    lower = min(x[0] for x in bounds)
    upper = max(x[1] for x in bounds)
    return (float(lower), float(upper), minimum, len(optimal))


class SmallSampleOracle(unittest.TestCase):
    def check(self, reports, target_from, target_to):
        result = algo.calculate(graph(reports, target_from, target_to)[0])
        expected = brute_force(reports, target_from, target_to)
        self.assertIsNotNone(result)
        if expected == None:
            self.assertTrue(result['unbounded'])
            return result, None
        if expected[0] == 'unbounded':
            self.assertTrue(result['unbounded'], result['reason'])
            self.assertIsNone(result['bounds'])
            self.assertTrue(result['reason'])
            return result, expected
        self.assertFalse(result['unbounded'], result['reason'])
        self.assertEqual(result['metrics']['最少剔除报告数'], expected[2])
        self.assertEqual(result['metrics']['最优方案数'], expected[3])
        self.assertAlmostEqual(result['bounds']['lower'], expected[0], places=9)
        self.assertAlmostEqual(result['bounds']['upper'], expected[1], places=9)
        # 两端剔除清单必须真的达到对应端点。
        for scheme in result['lower_witnesses']:
            self.assertEqual(scheme['lower'], result['bounds']['lower'])
        for scheme in result['upper_witnesses']:
            self.assertEqual(scheme['upper'], result['bounds']['upper'])
        return result, expected

    def test_compatible_chain(self):
        reports = [
            ('R1', 'A', 'B', 0, 1),
            ('R2', 'B', 'C', 2, 3),
        ]
        result, _ = self.check(reports, 'A', 'C')
        self.assertEqual(result['metrics']['最少剔除报告数'], 0)
        self.assertEqual(result['bounds']['lower_text'], '2')
        self.assertEqual(result['bounds']['upper_text'], '4')

    def test_different_removals_different_bounds(self):
        # 删不同报告会得出不同边界：三个最优方案，各自区间互不相同。
        reports = [
            ('R1', 'A', 'B', 0, 1),
            ('R2', 'B', 'C', 1, 1),
            ('R3', 'C', 'A', 1, 1),
        ]
        result, _ = self.check(reports, 'A', 'B')
        self.assertEqual(result['metrics']['最少剔除报告数'], 1)
        lower_sets = {tuple(s['removed_sources']) for s in result['lower_witnesses']}
        upper_sets = {tuple(s['removed_sources']) for s in result['upper_witnesses']}
        self.assertEqual(lower_sets, {('R1',)})
        self.assertEqual(upper_sets, {('R2',), ('R3',)})
        self.assertNotEqual(result['bounds']['lower'], result['bounds']['upper'])
        self.assertEqual(result['bounds']['lower_text'], '-2')
        self.assertEqual(result['bounds']['upper_text'], '1')

    def test_single_report_isolates_target_is_unbounded(self):
        # 目标工艺对分属两组互不相连的报告：任何剔除都不会接上，直接无界。
        reports = [
            ('R1', 'A', 'B', 0, 1),
            ('R2', 'C', 'D', 0, 1),
        ]
        result, _ = self.check(reports, 'A', 'D')
        self.assertTrue(result['unbounded'])
        self.assertIn('断开', result['reason'])
        self.assertIsNone(result['bounds'])

    def test_connected_pair_never_unbounded_by_removal(self):
        # 完整图连通时，最少剔除方案不可能断开目标工艺对（割边不在任何环上）。
        reports = [
            ('R1', 'A', 'B', 0, 1),
            ('R2', 'B', 'A', 2, 2),
            ('R3', 'B', 'A', 5, 5),
            ('R4', 'A', 'C', 0, 1),
        ]
        result, expected = self.check(reports, 'A', 'B')
        self.assertFalse(result['unbounded'])
        for scheme in result['schemes']:
            self.assertTrue(scheme['connected'])

    def test_target_missing(self):
        reports = [('R1', 'A', 'B', 0, 1)]
        snapshot = {
            'reports': [
                {'id': 0, 'source': r[0], 'process_a': r[1], 'process_b': r[2], 'low': r[3], 'high': r[4]}
                for r in reports
            ],
            'target': {'from': 'A', 'to': 'Z'},
        }
        result = algo.calculate(snapshot)
        self.assertTrue(result['unbounded'])
        self.assertIn('未出现', result['reason'])

    def test_random_against_brute_force(self):
        rng = random.Random(20261120)
        for _ in range(120):
            processes = ['A', 'B', 'C', 'D']
            reports = []
            for i in range(rng.choice([2, 3, 4, 5])):
                a, b = rng.sample(processes, 2)
                low = rng.choice([0, 1, 2, 5])
                reports.append((f'R{i}', a, b, low, low + rng.choice([0, 0, 1, 2])))
            target_from, target_to = rng.sample(processes, 2)
            self.check(reports, target_from, target_to)


class Validation(unittest.TestCase):
    def test_rejects_malformed_reports(self):
        base = {'source_key': 'S1', 'process_a': 'A', 'process_b': 'B', 'low': '0', 'high': '1'}
        self.assertEqual(validate_report(base)['low'], '0')
        for bad, needle in (
            ({**base, 'source_key': ''}, '来源'),
            ({**base, 'process_a': ''}, '不能为空'),
            ({**base, 'process_a': 'A', 'process_b': 'A'}, '必须不同'),
            ({**base, 'low': '2', 'high': '1'}, '不得大于'),
            ({**base, 'low': 'nan'}, '有限数值'),
            ({**base, 'low': 'inf'}, '有限数值'),
            ({**base, 'low': '1e12'}, '超出允许范围'),
            ({**base, 'low': '0.1234567891'}, '小数'),
            ({**base, 'low': True}, '有限数值'),
        ):
            with self.assertRaises(ValueError, msg=str(bad)) as ctx:
                validate_report(bad)
            self.assertIn(needle, str(ctx.exception))

    def test_snapshot_rejects_invalid_input(self):
        rows = [{'id': 1, 'source_key': 'S1', 'process_a': 'A', 'process_b': 'B', 'low': '0', 'high': '1', 'revision': 1}]
        with self.assertRaises(ValueError):
            snapshot_from_rows([], 'A', 'B')
        with self.assertRaises(ValueError):
            snapshot_from_rows(rows, 'A', 'A')
        too_many = [dict(rows[0], id=i, source_key=f'S{i}') for i in range(21)]
        with self.assertRaises(ValueError):
            snapshot_from_rows(too_many, 'A', 'B')

    def test_parameters_whitelist(self):
        self.assertEqual(validate_parameters({}), {})
        with self.assertRaises(ValueError):
            validate_parameters({'resamples': 100})

    def test_optimistic_revision(self):
        self.assertEqual(check_revision(2, 2), 3)
        self.assertEqual(check_revision(2, None), 2)
        with self.assertRaises(ValueError):
            check_revision(3, 2)


class FrozenSnapshot(unittest.TestCase):
    def test_edit_after_freeze_does_not_change_snapshot(self):
        rows = [
            {'id': 1, 'source_key': 'S1', 'process_a': 'A', 'process_b': 'B', 'low': '0', 'high': '1', 'revision': 1},
            {'id': 2, 'source_key': 'S2', 'process_a': 'B', 'process_b': 'C', 'low': '1', 'high': '2', 'revision': 1},
        ]
        snapshot = snapshot_from_rows(rows, 'A', 'C')
        before = algo.calculate(snapshot)
        rows[0]['low'] = '-10'
        rows[0]['high'] = '-10'
        rows[0]['revision'] = 2
        rows.append({'id': 3, 'source_key': 'S3', 'process_a': 'C', 'process_b': 'A', 'low': '99', 'high': '99', 'revision': 1})
        after = algo.calculate(snapshot)
        self.assertEqual(before['bounds'], after['bounds'])
        self.assertEqual(before['checksum'] if 'checksum' in before else None, None)
        # 冻结快照内容本身未被改动。
        self.assertEqual(snapshot['reports'][0]['low'], '0')
        self.assertEqual(len(snapshot['reports']), 2)

    def test_target_frozen_with_snapshot(self):
        rows = [{'id': 1, 'source_key': 'S1', 'process_a': 'A', 'process_b': 'B', 'low': '0', 'high': '1', 'revision': 1}]
        snapshot = snapshot_from_rows(rows, 'A', 'B')
        self.assertEqual(snapshot['target'], {'from': 'A', 'to': 'B'})


class CapacityAndTiming(unittest.TestCase):
    def test_full_load_twenty_reports_within_30_seconds(self):
        rng = random.Random(42)
        processes = [f'P{i}' for i in range(6)]
        reports = []
        for i in range(20):
            a, b = rng.sample(processes, 2)
            low = round(1 + rng.uniform(-0.3, 0.3), 3)
            high = round(low + rng.uniform(0, 0.2), 3)
            reports.append({'id': i, 'source': f'S{i}', 'process_a': a, 'process_b': b, 'low': low, 'high': high})
        snapshot = {'reports': reports, 'target': {'from': reports[0]['process_a'], 'to': reports[0]['process_b']}}
        started = time.monotonic()
        result = algo.calculate(snapshot)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 30.0)
        self.assertIn('最少剔除报告数', result['metrics'])
        if not result['unbounded']:
            self.assertLessEqual(result['bounds']['lower'], result['bounds']['upper'])

    def test_21_reports_rejected(self):
        reports = [
            {'id': i, 'source': f'S{i}', 'process_a': 'A', 'process_b': 'B', 'low': 0, 'high': 1}
            for i in range(21)
        ]
        snapshot = {'reports': reports, 'target': {'from': 'A', 'to': 'B'}}
        with self.assertRaises(algo.InputError):
            algo.calculate(snapshot)


if __name__ == '__main__':
    unittest.main()
