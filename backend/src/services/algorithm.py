"""工艺差值区间的相容性推断。

每条试印报告给出两道工艺的差值区间：ΔE(B) − ΔE(A) ∈ [low, high]。
记 x_p 为工艺 p 的（未知）差值水平，则报告等价于两条差分约束：

    x_B − x_A ≤ high      （A → B，权 high）
    x_A − x_B ≤ −low      （B → A，权 −low）

全部报告相容 ⇔ 约束图无负环。目标工艺对 from → to 的最紧上界是
from 到 to 的最短路；最紧下界是 to 到 from 最短路取负。若两者不连通，
该方向无界。

题目只接受“剔除报告数量最少且剩余区间相容”的方案；结果给出覆盖全部
最优方案的最紧上下界，以及两端各自的剔除清单。任一最优方案中目标工艺对
断开，则整体报无界。报告上限 20 份，满载求解硬上限 30 秒。

本模块只依赖 Python 标准库，便于在没有 NumPy/SciPy 的环境中独立验算。
"""

import math
import time
from decimal import Decimal, InvalidOperation

MAX_REPORTS = 20
MAX_ABS = Decimal('1000000000')
MAX_DECIMALS = 9
MAX_SECONDS = 30.0
_DEADLINE_CHECK_MASK = 63


class SolverTimeout(Exception):
    """最优方案枚举超过 30 秒硬上限。"""


class InputError(ValueError):
    """算法输入不合法。"""


def parse_decimal(value, label):
    """把外部输入转为有限 Decimal，并限制量级与小数位。"""
    if isinstance(value, bool):
        raise InputError(label + '必须是有限数值')
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, int):
        d = Decimal(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise InputError(label + '必须是有限数值')
        d = Decimal(str(value))
    elif isinstance(value, str):
        try:
            d = Decimal(value.strip())
        except (InvalidOperation, ValueError):
            raise InputError(label + '必须是有限数值')
    else:
        raise InputError(label + '必须是有限数值')
    if not d.is_finite():
        raise InputError(label + '必须是有限数值')
    if abs(d) > MAX_ABS:
        raise InputError(label + '超出允许范围')
    exponent = d.as_tuple().exponent
    if exponent < -MAX_DECIMALS:
        raise InputError(f'{label}最多保留{MAX_DECIMALS}位小数')
    return d


def decimal_text(value):
    """精确十进制文本，去掉多余的尾零。"""
    d = value.normalize()
    if d == 0:
        return '0'
    if d == d.to_integral_value():
        return format(d.to_integral_value(), 'f')
    return format(d, 'f')


_decimal = parse_decimal
_text = decimal_text


def normalize_snapshot(snapshot):
    """校验并规范化冻结快照，返回报告列表与目标工艺对。"""
    if not isinstance(snapshot, dict):
        raise InputError('输入快照格式错误')
    raw_reports = snapshot.get('reports')
    target = snapshot.get('target')
    if not isinstance(raw_reports, list) or not raw_reports:
        raise InputError('空批次无法分析')
    if len(raw_reports) > MAX_REPORTS:
        raise InputError(f'报告超过{MAX_REPORTS}份，拒绝排队')
    if not isinstance(target, dict):
        raise InputError('缺少目标工艺对')
    reports = []
    sources = set()
    for order, raw in enumerate(raw_reports):
        if not isinstance(raw, dict):
            raise InputError('报告格式错误')
        source = str(raw.get('source') or '').strip()
        process_a = str(raw.get('process_a') or '').strip()
        process_b = str(raw.get('process_b') or '').strip()
        if not source:
            raise InputError('每条报告必须带来源')
        if not process_a or not process_b:
            raise InputError('工艺名称不能为空')
        if process_a == process_b:
            raise InputError('同一报告的两道工艺必须不同')
        if source in sources:
            raise InputError('来源编号重复：' + source)
        sources.add(source)
        low = _decimal(raw.get('low'), '差值下界')
        high = _decimal(raw.get('high'), '差值上界')
        if low > high:
            raise InputError('差值下界不得大于上界：' + source)
        try:
            report_id = int(raw.get('id', order))
        except (TypeError, ValueError):
            report_id = order
        try:
            revision = int(raw.get('revision', 0))
        except (TypeError, ValueError):
            revision = 0
        reports.append({
            'order': order,
            'id': report_id,
            'source': source,
            'process_a': process_a,
            'process_b': process_b,
            'low': low,
            'high': high,
            'revision': revision,
        })
    target_from = str(target.get('from') or '').strip()
    target_to = str(target.get('to') or '').strip()
    if not target_from or not target_to:
        raise InputError('目标工艺不能为空')
    if target_from == target_to:
        raise InputError('目标两道工艺必须不同')
    return reports, {'from': target_from, 'to': target_to}


def _scale(reports):
    digits = 0
    for report in reports:
        for key in ('low', 'high'):
            exponent = report[key].as_tuple().exponent
            if exponent < 0:
                digits = max(digits, -exponent)
    scale = 10 ** digits
    return scale, digits


def _build_graph(reports):
    names = sorted({r['process_a'] for r in reports} | {r['process_b'] for r in reports})
    index = {name: i for i, name in enumerate(names)}
    scale, digits = _scale(reports)
    # 每条报告用一位 bit 表示，便于在分支限界中快速过滤。
    edges = []
    for i, report in enumerate(reports):
        bit = 1 << i
        a = index[report['process_a']]
        b = index[report['process_b']]
        low = int((report['low'] * scale).to_integral_value())
        high = int((report['high'] * scale).to_integral_value())
        edges.append((a, b, high, bit))
        edges.append((b, a, -low, bit))
    return names, index, edges, scale, digits


def _negative_cycle(size, edges, removed):
    """Bellman-Ford 检测负环，返回负环上报告的 bit 掩码；无负环返回 None。"""
    dist = [0] * size
    predecessor = [-1] * size
    predecessor_edge = [-1] * size
    changed_node = -1
    for _ in range(size):
        changed_node = -1
        for edge_index, (u, v, weight, bit) in enumerate(edges):
            if bit & removed:
                continue
            candidate = dist[u] + weight
            if candidate < dist[v]:
                dist[v] = candidate
                predecessor[v] = u
                predecessor_edge[v] = edge_index
                changed_node = v
        if changed_node < 0:
            return None
    if changed_node < 0:
        return None
    node = changed_node
    for _ in range(size):
        if predecessor[node] < 0:
            return None
        node = predecessor[node]
    mask = 0
    cursor = node
    guard = 0
    while True:
        edge_index = predecessor_edge[cursor]
        if edge_index < 0:
            return None
        mask |= edges[edge_index][3]
        cursor = predecessor[cursor]
        guard += 1
        if cursor == node or guard > size + 1:
            break
    return mask or None


def _shortest(size, edges, removed, start):
    """无负环图上的单源最短路（Bellman-Ford）。不可达返回 None。"""
    dist = [None] * size
    dist[start] = 0
    for _ in range(size - 1):
        changed = False
        for u, v, weight, bit in edges:
            if bit & removed:
                continue
            base = dist[u]
            if base is None:
                continue
            candidate = base + weight
            if dist[v] is None or candidate < dist[v]:
                dist[v] = candidate
                changed = True
        if not changed:
            break
    return dist


def _bits(mask):
    while mask:
        bit = mask & -mask
        yield bit
        mask ^= bit


def _greedy_upper(size, edges, report_count):
    """三种确定性贪心，取最小剔除数作为分支限界上界。"""
    best = report_count
    for strategy in ('first', 'last', 'degree'):
        removed = 0
        while True:
            cycle = _negative_cycle(size, edges, removed)
            if cycle is None:
                break
            if strategy == 'first':
                pick = cycle & -cycle
            elif strategy == 'last':
                pick = 1 << (cycle.bit_length() - 1)
            else:
                degree = {}
                for _, _, _, bit in edges:
                    if not (bit & removed):
                        degree[bit] = degree.get(bit, 0) + 1
                pick = max(_bits(cycle), key=lambda b: (degree.get(b, 0), b.bit_length()))
            removed |= pick
        best = min(best, removed.bit_count())
    return best


def _packing_lower_bound(size, edges, removed):
    """报告互不相交的负环个数，是最少仍需剔除数的合法下界。"""
    count = 0
    extra = removed
    while True:
        cycle = _negative_cycle(size, edges, extra)
        if cycle is None:
            return count
        count += 1
        extra |= cycle


def _enumerate_minimal(size, edges, report_count, started, deadline_seconds=MAX_SECONDS):
    """分支限界枚举全部最少剔除方案，返回 (剔除掩码集合, 搜索节点数, 最少剔除数)。"""
    best = _greedy_upper(size, edges, report_count)
    solutions = set()
    seen = set()
    nodes = 0

    def search(removed):
        nonlocal best, nodes
        nodes += 1
        if nodes & _DEADLINE_CHECK_MASK == 0 and time.monotonic() - started > deadline_seconds:
            raise SolverTimeout(f'最优方案枚举超过{deadline_seconds:g}秒上限')
        if removed in seen:
            return
        seen.add(removed)
        count = removed.bit_count()
        cycle = _negative_cycle(size, edges, removed)
        if cycle is None:
            if count < best:
                best = count
                solutions.clear()
                solutions.add(removed)
            elif count == best:
                solutions.add(removed)
            return
        if count >= best:
            return
        if count + _packing_lower_bound(size, edges, removed) > best:
            return
        branch = sorted(_bits(cycle), key=lambda b: (-(b.bit_length()), b))
        for bit in branch:
            search(removed | bit)

    search(0)
    return solutions, nodes, best


def _result_report(report):
    return {
        'id': report['id'],
        'source': report['source'],
        'process_a': report['process_a'],
        'process_b': report['process_b'],
        'low': float(report['low']),
        'high': float(report['high']),
        'low_text': _text(report['low']),
        'high_text': _text(report['high']),
        'revision': report['revision'],
    }


def _scheme(size, edges, removed, reports, scale, start, end):
    # scale 仅用于把整数最短路还原为十进制文本。
    removed_reports = [_result_report(reports[i]) for i in range(len(reports)) if removed >> i & 1]
    record = {
        'removed': removed_reports,
        'removed_ids': [r['id'] for r in removed_reports],
        'removed_sources': [r['source'] for r in removed_reports],
        'removed_count': len(removed_reports),
        'connected': False,
        'lower': None,
        'upper': None,
        'lower_text': None,
        'upper_text': None,
    }
    dist_from = _shortest(size, edges, removed, start)
    dist_to = _shortest(size, edges, removed, end)
    if dist_from[end] is None or dist_to[start] is None:
        return record
    lower = Decimal(-dist_to[start]) / Decimal(scale)
    upper = Decimal(dist_from[end]) / Decimal(scale)
    record.update({
        'connected': True,
        'lower': float(lower),
        'upper': float(upper),
        'lower_text': _text(lower),
        'upper_text': _text(upper),
    })
    return record


def _empty_bounds(reason, metrics, schemes=None):
    schemes = schemes or []
    rows = []
    for i, scheme in enumerate(schemes, 1):
        removed_text = '、'.join(scheme['removed_sources']) or '无'
        rows.append([
            i,
            removed_text,
            scheme['lower_text'] if scheme['lower_text'] is not None else '无界',
            scheme['upper_text'] if scheme['upper_text'] is not None else '无界',
            '是' if scheme['connected'] else '否',
        ])
    result = {
        'unbounded': True,
        'bounds': None,
        'reason': reason,
        'metrics': metrics,
        'schemes': schemes,
        'lower_witnesses': [],
        'upper_witnesses': [],
        'disconnected_schemes': [s for s in schemes if not s['connected']],
        'interpretation': reason,
        'table_title': '最优剔除方案与各自边界' if schemes else '',
        'columns': ['方案', '剔除报告（来源）', '推断下界', '推断上界', '目标工艺对连通'],
        'rows': rows,
    }
    return result


def calculate(snapshot, parameters=None):
    """计算冻结快照的差值范围，返回可 JSON 序列化的结果。"""
    started = time.monotonic()
    reports, target = normalize_snapshot(snapshot)
    report_count = len(reports)
    deadline_seconds = MAX_SECONDS
    names, index, edges, scale, digits = _build_graph(reports)
    base_metrics = {
        '报告数': report_count,
        '目标工艺对': f"{target['from']} → {target['to']}",
        '计算方法': '差值约束负环 + 最小剔除分支限界 + 最短路',
        '求解上限': f'{deadline_seconds:g}秒',
    }
    if target['from'] not in index or target['to'] not in index:
        reason = '目标工艺未出现在任何报告中，差值无界。'
        result = _empty_bounds(reason, base_metrics)
        result['elapsed_ms'] = round((time.monotonic() - started) * 1000, 3)
        return result
    start = index[target['from']]
    end = index[target['to']]

    # 报告边总是双向的；若完整图已不连通，任何剔除方案都不可能连通。
    full_reach = _shortest(len(names), edges, 0, start)
    if full_reach[end] is None:
        reason = f"全部报告的约束图已使 {target['from']} 与 {target['to']} 断开，任何方案均无界。"
        result = _empty_bounds(reason, base_metrics)
        result['elapsed_ms'] = round((time.monotonic() - started) * 1000, 3)
        return result

    try:
        solutions, nodes, best = _enumerate_minimal(len(names), edges, report_count, started, deadline_seconds)
    except SolverTimeout:
        raise
    schemes = []
    for removed in sorted(solutions, key=lambda mask: tuple(i for i in range(report_count) if mask >> i & 1)):
        schemes.append(_scheme(len(names), edges, removed, reports, scale, start, end))
    schemes.sort(key=lambda s: tuple(s['removed_ids']))
    base_metrics.update({
        '最少剔除报告数': best,
        '最优方案数': len(schemes),
        '枚举节点数': nodes,
    })
    if not schemes:
        result = _empty_bounds('没有可行的剔除方案，差值无界。', base_metrics)
        result['elapsed_ms'] = round((time.monotonic() - started) * 1000, 3)
        return result
    disconnected = [s for s in schemes if not s['connected']]
    if disconnected:
        reason = f"存在{len(disconnected)}个最少剔除方案使目标工艺对断开，差值无界。"
        metrics = dict(base_metrics)
        metrics.update({'推断下界': '无界', '推断上界': '无界'})
        result = _empty_bounds(reason, metrics, schemes)
        result['elapsed_ms'] = round((time.monotonic() - started) * 1000, 3)
        return result
    lower = min(s['lower'] for s in schemes)
    upper = max(s['upper'] for s in schemes)
    # 用精确十进制文本展示两端最紧边界，避免浮点舍入。
    lower_text = min((s for s in schemes if s['lower'] == lower), key=lambda s: tuple(s['removed_ids']))['lower_text']
    upper_text = max((s for s in schemes if s['upper'] == upper), key=lambda s: tuple(s['removed_ids']))['upper_text']
    lower_witnesses = [s for s in schemes if s['lower'] == lower]
    upper_witnesses = [s for s in schemes if s['upper'] == upper]
    metrics = dict(base_metrics)
    metrics.update({'推断下界': lower, '推断上界': upper})
    rows = []
    for i, scheme in enumerate(schemes, 1):
        removed_text = '、'.join(scheme['removed_sources']) or '无'
        rows.append([i, removed_text, scheme['lower_text'], scheme['upper_text'], '是'])
    if best == 0:
        interpretation = (
            f"全部{report_count}份报告区间相容，无需剔除；"
            f"目标工艺对 {target['from']} → {target['to']} 的差值最紧范围为 "
            f"[{lower_text}, {upper_text}]。"
        )
    else:
        interpretation = (
            f"剔除最少{best}份报告后共有{len(schemes)}个最优方案；覆盖全部方案，"
            f"目标工艺对 {target['from']} → {target['to']} 的差值最紧范围为 "
            f"[{lower_text}, {upper_text}]。"
        )
    caveat = (
        '仅采用报告给出的区间约束；上下界是所有最少剔除方案的并集外包，'
        '单个方案自身区间可能更窄。剔除只按数量最少，未评价报告质量。'
    )
    return {
        'unbounded': False,
        'bounds': {'lower': lower, 'upper': upper, 'lower_text': lower_text, 'upper_text': upper_text},
        'reason': '',
        'metrics': metrics,
        'schemes': schemes,
        'lower_witnesses': lower_witnesses,
        'upper_witnesses': upper_witnesses,
        'disconnected_schemes': [],
        'interpretation': interpretation,
        'caveat': caveat,
        'table_title': '最少剔除方案与各自边界',
        'columns': ['方案', '剔除报告（来源）', '推断下界', '推断上界', '目标工艺对连通'],
        'rows': rows,
        'scale_digits': digits,
        'elapsed_ms': round((time.monotonic() - started) * 1000, 3),
    }
