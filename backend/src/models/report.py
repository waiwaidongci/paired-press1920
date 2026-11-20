from sqlalchemy import Column,Integer,String,ForeignKey,UniqueConstraint
from src.config.database import Base
class TrialReport(Base):
    __tablename__='trial_reports'
    __table_args__=(UniqueConstraint('study_id','source_key'),)
    id=Column(Integer,primary_key=True)
    study_id=Column(Integer,ForeignKey('studies.id'),nullable=False)
    source_key=Column(String(100),nullable=False)
    process_a=Column(String(40),nullable=False)
    process_b=Column(String(40),nullable=False)
    low=Column(String(60),nullable=False)
    high=Column(String(60),nullable=False)
    revision=Column(Integer,nullable=False,default=1)
Report=TrialReport
