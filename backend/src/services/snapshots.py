"""冻结快照的纯逻辑，方便独立验算。"""

from src.config.domain import DOMAIN
from src.services.algorithm import normalize_snapshot


def snapshot_from_rows(rows, target_from, target_to):
    """把当前一版报告与目标工艺对冻结为快照。

    rows 为数据库行的普通字典列表（含 revision）。函数不读取数据库，
    因此调用之后外部再改录不会改变已冻结的快照。
    """
    if not rows:
        raise ValueError('先录入至少一条试印报告')
    if len(rows) > DOMAIN['max_reports']:
        raise ValueError(f"报告超过{DOMAIN['max_reports']}份，拒绝排队")
    process_from = str(target_from or '').strip()
    process_to = str(target_to or '').strip()
    if not process_from or not process_to:
        raise ValueError('请选择两道目标工艺')
    if process_from == process_to:
        raise ValueError('目标两道工艺必须不同')
    processes = {row['process_a'] for row in rows} | {row['process_b'] for row in rows}
    missing = [p for p in (process_from, process_to) if p not in processes]
    if missing:
        raise ValueError('目标工艺未出现在本批次报告中：' + '、'.join(missing))
    snapshot = {
        'version': 1,
        'target': {'from': process_from, 'to': process_to},
        'reports': [
            {
                'id': row['id'],
                'source': row['source_key'],
                'process_a': row['process_a'],
                'process_b': row['process_b'],
                'low': row['low'],
                'high': row['high'],
                'revision': row['revision'],
            }
            for row in rows
        ],
    }
    # 发布前完成同一套输入校验；不合法即抛 ValueError，任务不会进入队列。
    normalize_snapshot(snapshot)
    return snapshot
