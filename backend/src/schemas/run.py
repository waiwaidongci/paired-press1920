from pydantic import BaseModel,Field
class TargetPair(BaseModel):
    process_from:str=Field(min_length=1,max_length=40)
    process_to:str=Field(min_length=1,max_length=40)
class PairedRunInput(BaseModel):
    study_id:int=Field(gt=0)
    name:str=Field(min_length=2,max_length=120)
    target:TargetPair
    parameters:dict=Field(default_factory=dict)
RunInput=PairedRunInput
