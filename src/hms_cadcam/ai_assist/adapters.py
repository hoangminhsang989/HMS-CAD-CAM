"""Production-safe draft adapters: validate/set selected fields only, never save."""
from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Mapping, Protocol
from hms_cadcam.ai_assist.cutting_advisor import OperationFamily

class ProductionDraft(Protocol):
    operation_id: str
    revision: int
    def snapshot_metric(self) -> Mapping[str, float|str]: ...
    def validate_field(self, name:str, value:float)->bool: ...
    def set_draft_field(self, name:str, value:float)->None: ...

@dataclass(frozen=True, slots=True)
class AdapterRequest: project_id:str; operation_id:str; editor_id:str; revision:int; family:OperationFamily; snapshot:Mapping[str,float|str]; digest:str
@dataclass(frozen=True, slots=True)
class ApplyResult: applied:tuple[str,...]; rejected:tuple[str,...]; status:str

class OperationAdapter:
    family: OperationFamily
    def __init__(self,draft:ProductionDraft,project_id:str,editor_id:str)->None: self._draft=draft; self._project_id=project_id; self._editor_id=editor_id
    def build_request(self)->AdapterRequest:
        snapshot=dict(self._draft.snapshot_metric()); encoded=json.dumps(snapshot,sort_keys=True,separators=(",",":"),ensure_ascii=True)
        return AdapterRequest(self._project_id,self._draft.operation_id,self._editor_id,self._draft.revision,self.family,snapshot,sha256(encoded.encode()).hexdigest())
    def selective_apply(self, ownership:AdapterRequest, values:Mapping[str,float], selected:set[str])->ApplyResult:
        current=self.build_request()
        if current.operation_id!=ownership.operation_id or current.revision!=ownership.revision or current.digest!=ownership.digest: return ApplyResult((),(),"STALE_RESULT_DISCARDED")
        applied=[]; rejected=[]
        for name in sorted(selected):
            if name not in values: continue
            if self._draft.validate_field(name,values[name]): self._draft.set_draft_field(name,values[name]); applied.append(name)
            else: rejected.append(name)
        return ApplyResult(tuple(applied),tuple(rejected),"APPLIED" if applied else "NOT_APPLIED")
class MillingAdapter(OperationAdapter): family=OperationFamily.MILLING
class DrillingAdapter(OperationAdapter): family=OperationFamily.DRILLING
class TurningAdapter(OperationAdapter): family=OperationFamily.TURNING
