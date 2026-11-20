import json
from sqlalchemy import select,func
from src.config.database import Base,engine,Session
from src.config.domain import DOMAIN
from src.models.user import User
from src.models.study import Study
from src.models.report import Report
from src.utils.security import hash_password

# 合成试印报告：report = (source, process_a, process_b, low, high)
# 批次 1：6 份报告、3 个最少剔除方案，删不同报告会得到不同边界。
# 批次 2：全部报告相容，无需剔除。
# 批次 3：目标工艺对分属断开的两组，差值无界。
SEEDS = [
    [
        ('PR-01', '米白', '棉纸', '0.10', '0.30'),
        ('PR-02', '棉纸', '轻涂', '0.20', '0.40'),
        ('PR-03', '米白', '轻涂', '0.10', '0.20'),
        ('PR-04', '轻涂', '再生', '-0.20', '0.10'),
        ('PR-05', '米白', '再生', '-0.10', '0.30'),
        ('PR-06', '棉纸', '再生', '0.05', '0.25'),
    ],
    [
        ('SM-01', '米白', '棉纸', '0.10', '0.30'),
        ('SM-02', '棉纸', '轻涂', '0.20', '0.40'),
    ],
    [
        ('DB-01', '米白', '棉纸', '0.10', '0.30'),
        ('DB-02', '轻涂', '再生', '-0.20', '0.10'),
    ],
]

def seed():
    Base.metadata.create_all(engine)
    with Session() as db:
        for role in ('admin','analyst','viewer'):
            email=role+'@demo.local'
            if not db.scalar(select(User).where(User.email==email)): db.add(User(email=email,role=role,password_hash=hash_password('Research2026!')))
        for k,name in enumerate(DOMAIN['names']):
            study=db.scalar(select(Study).where(Study.name==name))
            if not study:
                study=Study(name=name,source_filename=DOMAIN['slug']+f'-202611-{k+1}.csv',description='独立合成业务观测 / 仅用于流程演示');db.add(study);db.flush()
            for source,a,b,low,high in SEEDS[k]:
                if not db.scalar(select(Report.id).where(Report.study_id==study.id,Report.source_key==source)):
                    db.add(Report(study_id=study.id,source_key=source,process_a=a,process_b=b,low=low,high=high,revision=1))
        db.commit()
        counts={model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in (User,Study,Report)}
        print(json.dumps(counts));return counts
if __name__=='__main__': seed()
