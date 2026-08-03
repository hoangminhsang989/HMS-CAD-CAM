"""Deterministic, immutable three-family Stage 13B numerical engine."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from hashlib import sha256
from math import isfinite, pi
from typing import Mapping
from hms_cadcam.ai_assist.model_loader import CuttingModel

class OperationFamily(StrEnum): MILLING="milling"; DRILLING="drilling"; TURNING="turning"
class RecommendationProfile(StrEnum): CONSERVATIVE="CONSERVATIVE"; BALANCED="BALANCED"; PRODUCTIVE="PRODUCTIVE"
class RecommendationStatus(StrEnum): READY="READY"; UNSUPPORTED_INPUT="UNSUPPORTED_INPUT"

@dataclass(frozen=True, slots=True)
class CuttingRequest:
    correlation_id: str; family: OperationFamily; material_group: str; tool_material: str; diameter_mm: float; profile: RecommendationProfile=RecommendationProfile.BALANCED
    flute_count: int | None=None; rigidity: str="NORMAL"; machine_max_rpm: float | None=None; machine_max_feed_mm_min: float | None=None
    requested_axial_depth_mm: float | None=None; requested_radial_engagement_mm: float | None=None; requested_peck_depth_mm: float | None=None; requested_depth_of_cut_mm: float | None=None
    def __post_init__(self) -> None:
        if not self.correlation_id or self.material_group not in {"ISO_P","ISO_M","ISO_K","ISO_N","ISO_S","ISO_H"} or self.tool_material not in {"HSS","CARBIDE"} or not isfinite(self.diameter_mm) or self.diameter_mm <= 0: raise ValueError("INVALID_INPUT")
        if self.flute_count is not None and (type(self.flute_count) is not int or self.flute_count <= 0): raise ValueError("INVALID_INPUT")
        for value in (self.machine_max_rpm,self.machine_max_feed_mm_min,self.requested_axial_depth_mm,self.requested_radial_engagement_mm,self.requested_peck_depth_mm,self.requested_depth_of_cut_mm):
            if value is not None and (not isfinite(value) or value <= 0): raise ValueError("INVALID_INPUT")
    @property
    def digest(self) -> str: return sha256(repr(self).encode("utf-8")).hexdigest()

@dataclass(frozen=True, slots=True)
class CuttingRecommendation:
    status: RecommendationStatus; correlation_id: str; input_digest: str; raw: Mapping[str,float]; values: Mapping[str,float]; clamps: tuple[str,...]=(); confidence: float=0.0

def _nominal(model: CuttingModel, family: OperationFamily, name: str) -> float: return float(model.data["families"][family.value][name]["nominal"])
def _maximum(model: CuttingModel, family: OperationFamily, name: str) -> float: return float(model.data["families"][family.value][name]["maximum"])
def _round(value: float) -> float: return round(max(0.0, value), 4)

def recommend(model: CuttingModel, request: CuttingRequest) -> CuttingRecommendation:
    """Calculate only fields with complete family inputs; never invent data."""
    if request.family is OperationFamily.MILLING and request.flute_count is None: raise ValueError("INVALID_INPUT")
    m = float(model.data["materials"][request.material_group]); t = float(model.data["tool_materials"][request.tool_material]); p = float(model.data["profiles"][request.profile.value]); r = float(model.data["rigidity"].get(request.rigidity, 0.0))
    if r <= 0: raise ValueError("UNSUPPORTED_INPUT")
    speed = _nominal(model, request.family, "cutting_speed") * m * t * p * r
    rpm_raw = 1000.0 * speed / (pi * request.diameter_mm)
    clamps: list[str] = []; rpm = rpm_raw
    if request.machine_max_rpm is not None and rpm > request.machine_max_rpm: rpm=request.machine_max_rpm; clamps.append("SPINDLE_CLAMPED_TO_MACHINE_MAX")
    feed_name = "feed_per_tooth" if request.family is OperationFamily.MILLING else "feed_per_revolution"
    feed_unit = _nominal(model, request.family, feed_name) * p * r
    multiplier = request.flute_count if request.family is OperationFamily.MILLING else 1
    feed_raw = rpm_raw * feed_unit * multiplier; feed=feed_raw
    if request.machine_max_feed_mm_min is not None and feed > request.machine_max_feed_mm_min: feed=request.machine_max_feed_mm_min; clamps.append("FEED_CLAMPED_TO_MACHINE_MAX")
    raw={"spindle_rpm":rpm_raw,"linear_feed_mm_min":feed_raw, feed_name+"_mm":feed_unit}; values={"spindle_rpm":_round(rpm),"linear_feed_mm_min":_round(feed),feed_name+"_mm":_round(feed_unit)}
    specs = (("requested_axial_depth_mm","axial_depth_factor","axial_stepdown_mm"),("requested_radial_engagement_mm","radial_engagement_factor","radial_stepover_mm"),("requested_peck_depth_mm","peck_depth_factor","peck_depth_mm"),("requested_depth_of_cut_mm","depth_of_cut_factor","depth_of_cut_mm"))
    for request_name, factor_name, result_name in specs:
        requested=getattr(request,request_name)
        if requested is not None and factor_name in model.data["families"][request.family.value]:
            limit=request.diameter_mm*_nominal(model,request.family,factor_name)*p*r; final=min(requested,limit)
            raw[result_name]=requested; values[result_name]=_round(final)
            if final < requested: clamps.append(result_name.upper()+"_CLAMPED_TO_MODEL_SAFE_MAX")
    if request.family is OperationFamily.MILLING: values["plunge_feed_mm_min"]=_round(values["linear_feed_mm_min"]*_nominal(model,request.family,"plunge_factor"))
    confidence=max(0.1, 1.0-0.08*len(clamps))
    return CuttingRecommendation(RecommendationStatus.READY,request.correlation_id,request.digest,raw,values,tuple(clamps),confidence)

__all__=["CuttingRequest","CuttingRecommendation","OperationFamily","RecommendationProfile","RecommendationStatus","recommend"]
