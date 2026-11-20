import csv
import io
import json
import os

from redis import Redis
from sqlalchemy import select
from fastapi import HTTPException

from src.models.run import Run
from src.models.review import Review
from src.services import studies
from src.services.algorithm import InputError
from src.services.domain_validation import validate_parameters
from src.services.snapshots import snapshot_from_rows
from src.services.audits import log
from src.utils.serializers import record, now, checksum

QUEUE_KEY = 'analysis-queue'


def _redis():
    return Redis.from_url(os.environ['REDIS_URL'])


def _snapshot(db, study_id, target_from, target_to):
    rows = studies.reports(db, study_id)
    try:
        return snapshot_from_rows(rows, target_from, target_to)
    except (ValueError, InputError) as e:
        raise HTTPException(422, str(e))


def create(db, data, user):
    studies.require(db, data.study_id)
    try:
        params = validate_parameters(data.parameters)
    except (ValueError, TypeError) as e:
        raise HTTPException(422, str(e))
    snapshot = _snapshot(
        db, data.study_id, data.target.process_from, data.target.process_to
    )
    client = _redis()
    run = Run(
        study_id=data.study_id,
        name=data.name,
        parameters=params,
        snapshot=snapshot,
        checksum=checksum(snapshot),
        created_at=now(),
        status='queued',
    )
    db.add(run)
    db.flush()
    log(db, user, 'run.queue', str(run.id))
    db.commit()
    # 先提交再入队，worker 不会取到尚未落库的任务。
    client.rpush(QUEUE_KEY, run.id)
    return record(run)


def get(db, id):
    run = db.get(Run, id)
    if not run:
        raise HTTPException(404, '运行不存在')
    result = record(run)
    review = db.scalar(select(Review).where(Review.run_id == id))
    result['review'] = record(review) if review else None
    return result


def list_runs(db):
    return [get(db, x) for x in db.scalars(select(Run.id).order_by(Run.id.desc()))]


def approve(db, id, data, user):
    run = db.get(Run, id)
    if not run:
        raise HTTPException(404, '运行不存在')
    if run.status != 'completed':
        raise HTTPException(409, '只能复核已完成运行')
    if db.scalar(select(Review).where(Review.run_id == id)):
        raise HTTPException(409, '已复核，记录不可覆盖')
    obj = Review(run_id=id, note=data.note, author=user.email, created_at=now())
    db.add(obj)
    log(db, user, 'review.approve', f'{id}: {data.note}')
    db.commit()
    return record(obj)


def _scheme_text(scheme):
    removed = '、'.join(scheme['removed_sources']) or '无'
    bound = lambda key: scheme[key] if scheme[key] is not None else '无界'
    return removed, bound('lower_text'), bound('upper_text')


def export(db, id, user):
    r = get(db, id)
    if r['status'] != 'completed':
        raise HTTPException(409, '尚无完成结果')
    if user.role == 'viewer' and not r['review']:
        raise HTTPException(403, '观察员只能导出已复核结果')
    result = r['result']
    stream = io.StringIO()
    w = csv.writer(stream)
    w.writerow(['运行编号', '输入校验SHA256', '指标', '值'])
    for k, v in result['metrics'].items():
        w.writerow([id, r['checksum'], k, json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v])
    bounds = result.get('bounds')
    legacy = 'bounds' not in result and 'unbounded' not in result
    if legacy:
        w.writerow([id, r['checksum'], '结果版本', '旧版方法结果，仅保留原始指标'])
    elif bounds:
        w.writerow([id, r['checksum'], '覆盖全部方案的下界', bounds['lower_text']])
        w.writerow([id, r['checksum'], '覆盖全部方案的上界', bounds['upper_text']])
        for label, key in (('下界剔除清单', 'lower_witnesses'), ('上界剔除清单', 'upper_witnesses')):
            for scheme in result.get(key, []):
                removed, low, high = _scheme_text(scheme)
                w.writerow([id, r['checksum'], label, f"剔除：{removed}；该方案 [{low}, {high}]"])
    else:
        w.writerow([id, r['checksum'], '推断范围', '无界'])
        w.writerow([id, r['checksum'], '无界原因', result.get('reason', '')])
        for scheme in result.get('disconnected_schemes', []):
            w.writerow([id, r['checksum'], '断开方案', '、'.join(scheme['removed_sources']) or '无'])
    for scheme in result.get('schemes', []):
        removed, low, high = _scheme_text(scheme)
        w.writerow([id, r['checksum'], '最少剔除方案', f"剔除：{removed}；[{low}, {high}]"])
    w.writerow([id, r['checksum'], '解释', result.get('interpretation', '')])
    log(db, user, 'run.export', id)
    db.commit()
    return '﻿' + stream.getvalue()
