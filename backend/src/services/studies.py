from fastapi import HTTPException
from sqlalchemy import select,func
from src.models.study import Study
from src.models.report import Report
from src.services.domain_validation import (
    MAX_REPORTS_PER_STUDY, check_revision, validate_report,
)
from src.services.audits import log
from src.utils.serializers import record

def list_studies(db):
    data=[]
    for s in db.scalars(select(Study).order_by(Study.id)):
        x=record(s);x['row_count']=db.scalar(select(func.count()).select_from(Report).where(Report.study_id==s.id));data.append(x)
    return data

def create(db,data,user):
    if db.scalar(select(Study).where(Study.name==data.name)): raise HTTPException(409,'批次名称已存在')
    s=Study(**data.model_dump());db.add(s);log(db,user,'study.create',data.name);db.commit();return record(s)

def require(db,id):
    s=db.get(Study,id)
    if not s: raise HTTPException(404,'业务批次不存在')
    return s

def reports(db,id):
    require(db,id);return [record(x) for x in db.scalars(select(Report).where(Report.study_id==id).order_by(Report.id))]

def save_report(db,id,data,user,row_id=None):
    """新增或修改报告；带版本号时做乐观并发检查。"""
    require(db,id)
    try: values=validate_report(data.model_dump())
    except (ValueError,TypeError) as e: raise HTTPException(422,str(e))
    other=db.scalar(select(Report).where(Report.study_id==id,Report.source_key==values['source_key']))
    if other and other.id!=row_id: raise HTTPException(409,'报告来源编号重复')
    expected=data.expected_revision
    if row_id:
        row=db.get(Report,row_id)
        if not row or row.study_id!=id: raise HTTPException(404,'报告不存在')
        try: next_revision=check_revision(row.revision,expected)
        except ValueError as e: raise HTTPException(409,str(e))
        row.source_key=values['source_key'];row.process_a=values['process_a']
        row.process_b=values['process_b'];row.low=values['low'];row.high=values['high']
        row.revision=next_revision
    else:
        if expected is not None: raise HTTPException(409,'新增报告不应携带版本号')
        count=db.scalar(select(func.count()).select_from(Report).where(Report.study_id==id))
        if count>=MAX_REPORTS_PER_STUDY: raise HTTPException(422,f'小型研究最多{MAX_REPORTS_PER_STUDY}份报告')
        row=Report(study_id=id,source_key=values['source_key'],process_a=values['process_a'],
                   process_b=values['process_b'],low=values['low'],high=values['high'],revision=1)
        db.add(row)
    db.flush()
    log(db,user,'report.save',f"{id}/{values['source_key']} v{row.revision}")
    db.commit();return record(row)

def delete_report(db,id,row_id,user):
    row=db.get(Report,row_id)
    if not row or row.study_id!=id: raise HTTPException(404,'报告不存在')
    source=row.source_key
    db.delete(row);log(db,user,'report.delete',f'{id}/{source}');db.commit();return {'deleted':True}
