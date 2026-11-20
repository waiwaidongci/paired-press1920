from pydantic import BaseModel,Field
class TrialReportInput(BaseModel):
    source_key:str=Field(min_length=1,max_length=100)
    process_a:str=Field(min_length=1,max_length=40)
    process_b:str=Field(min_length=1,max_length=40)
    low:str|float
    high:str|float
    expected_revision:int|None=Field(default=None,ge=1)
ReportInput=TrialReportInput
