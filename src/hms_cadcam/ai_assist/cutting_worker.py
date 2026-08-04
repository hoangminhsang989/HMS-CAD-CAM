"""Stdlib-only JSONL worker for Stage 13B; it owns no UI, network, or storage."""
from __future__ import annotations
import json
from pathlib import Path
import sys
from hms_cadcam.ai_assist.cutting_advisor import CuttingRequest, OperationFamily, RecommendationProfile, recommend
from hms_cadcam.ai_assist.model_loader import ModelLoadError, load_canonical_model
PROTOCOL="stage13b-cutting-advisor-v1"

def _send(kind: str, correlation_id: str="", payload: object=None) -> None:
    sys.stdout.write(json.dumps({"protocol":PROTOCOL,"type":kind,"correlation_id":correlation_id,"payload":payload},separators=(",",":"),sort_keys=True)+"\n"); sys.stdout.flush()
def _main() -> int:
    _send("HELLO",payload={"protocol":PROTOCOL})
    manifest=Path(__file__).with_name("models")/"cutting_parameters_v1.manifest.json"
    try: model=load_canonical_model(manifest)
    except ModelLoadError as error: _send("ERROR",payload=str(error)); return 2
    _send("READY",payload={"model_id":model.model_id,"model_version":model.model_version,"sha256":model.sha256,"supported_families":["milling","drilling","turning"]})
    for line in sys.stdin:
        if len(line.encode("utf-8"))>65_536: _send("ERROR",payload="MESSAGE_TOO_LARGE"); continue
        try: message=json.loads(line)
        except json.JSONDecodeError: _send("ERROR",payload="INVALID_JSON"); continue
        if not isinstance(message,dict) or message.get("protocol")!=PROTOCOL: _send("ERROR",payload="PROTOCOL_MISMATCH"); continue
        kind=message.get("type"); correlation=str(message.get("correlation_id", ""))
        if kind=="SHUTDOWN": return 0
        if kind=="CANCEL": _send("CANCEL",correlation,{"cancelled":True}); continue
        if kind!="RECOMMEND_REQUEST": _send("ERROR",correlation,"UNKNOWN_MESSAGE"); continue
        try:
            payload=message["payload"]
            request=CuttingRequest(correlation_id=correlation,family=OperationFamily(payload["family"]),material_group=payload["material_group"],tool_material=payload["tool_material"],diameter_mm=float(payload["diameter_mm"]),flute_count=payload.get("flute_count"),profile=RecommendationProfile(payload.get("profile","BALANCED")),rigidity=payload.get("rigidity","NORMAL"),machine_max_rpm=payload.get("machine_max_rpm"),machine_max_feed_mm_min=payload.get("machine_max_feed_mm_min"),requested_axial_depth_mm=payload.get("requested_axial_depth_mm"),requested_radial_engagement_mm=payload.get("requested_radial_engagement_mm"),requested_peck_depth_mm=payload.get("requested_peck_depth_mm"),requested_depth_of_cut_mm=payload.get("requested_depth_of_cut_mm"))
            result=recommend(model,request)
            _send("RECOMMEND_RESULT",correlation,{"input_digest":result.input_digest,"raw":dict(result.raw),"values":dict(result.values),"clamps":result.clamps,"confidence":result.confidence})
        except (KeyError,TypeError,ValueError) as error: _send("ERROR",correlation,str(error))
    return 0
if __name__=="__main__": raise SystemExit(_main())
