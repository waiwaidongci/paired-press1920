"""严格的输入校验：失败即拒绝，不进入队列。

这些函数只做纯计算，便于在没有数据库的环境中独立验算。
"""


from src.config.domain import DOMAIN
from src.services.algorithm import InputError, decimal_text, parse_decimal

PROCESS_MAX = 40
SOURCE_MAX = 100
MAX_REPORTS_PER_STUDY = 200


def _process(value, label):
    if not isinstance(value, str):
        raise ValueError(label + '必须为文本')
    name = value.strip()
    if not name:
        raise ValueError(label + '不能为空')
    if len(name) > PROCESS_MAX:
        raise ValueError(f'{label}最多{PROCESS_MAX}个字符')
    return name


def validate_report(data):
    """校验一份报告，返回规范化的字段（区间以精确十进制文本保存）。"""
    if not isinstance(data, dict):
        raise ValueError('报告格式错误')
    source = data.get('source_key')
    if not isinstance(source, str):
        raise ValueError('报告来源编号必须为文本')
    source = source.strip()
    if not source:
        raise ValueError('报告来源编号不能为空')
    if len(source) > SOURCE_MAX:
        raise ValueError(f'报告来源编号最多{SOURCE_MAX}个字符')
    process_a = _process(data.get('process_a'), '工艺 A')
    process_b = _process(data.get('process_b'), '工艺 B')
    if process_a == process_b:
        raise ValueError('同一报告的两道工艺必须不同')
    try:
        low = parse_decimal(data.get('low'), '差值下界')
        high = parse_decimal(data.get('high'), '差值上界')
    except InputError as e:
        raise ValueError(str(e))
    if low > high:
        raise ValueError('差值下界不得大于上界')
    return {
        'source_key': source,
        'process_a': process_a,
        'process_b': process_b,
        'low': decimal_text(low),
        'high': decimal_text(high),
    }


def check_revision(current, expected):
    """乐观并发：版本不一致就拒绝，避免覆盖他人的改录。"""
    if expected is None:
        return current
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
        raise ValueError('版本号必须为正整数')
    if expected != current:
        raise ValueError(f'报告已被他人修改（当前版本 {current}），请刷新后重试')
    return current + 1


def validate_parameters(params):
    """分析参数严格白名单；本方法不接受任何可调参数。"""
    defaults = DOMAIN['defaults']
    if not isinstance(params, dict) or set(params) - set(defaults):
        raise ValueError('存在未知分析参数')
    return defaults | params
