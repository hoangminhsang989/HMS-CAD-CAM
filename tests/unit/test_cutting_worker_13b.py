"""Real subprocess acceptance for the Stage 13B JSONL worker."""
from __future__ import annotations
import os
from pathlib import Path
import pytest
from hms_cadcam.ai_assist.cutting_supervisor import CuttingWorkerSupervisor
from hms_cadcam.ai_assist.policy import AiTier

ROOT=Path(__file__).parents[2]
PYTHON=Path("E:/CAD_CAM_Project/.venv/Scripts/python.exe")
WORKER=ROOT/"src/hms_cadcam/ai_assist/cutting_worker.py"
def started():
 s=CuttingWorkerSupervisor(PYTHON,WORKER);result=s.start(AiTier.LITE);assert result.started;assert result.process_id and s.pid==result.process_id;return s
def payload():return {"family":"milling","material_group":"ISO_P","tool_material":"CARBIDE","diameter_mm":10,"flute_count":4,"profile":"BALANCED"}
def test_real_hello_ready_and_identity():
 s=started();assert s.has_worker;s.shutdown();assert not s.has_worker
def test_real_recommendation_round_trip():
 s=started();result=s.recommend("round-trip",payload());assert result.kind=="RECOMMEND_RESULT" and result.payload["values"]["spindle_rpm"]>0;s.shutdown()
def test_real_cancel_and_shutdown_reaps_pid():
 s=started();pid=s.pid;s.cancel();s.shutdown();assert not s.has_worker
 with pytest.raises(OSError): os.kill(pid,0)
def test_real_protocol_error_is_structured():
 s=started();result=s.recommend("bad",{"family":"bad"});assert result.kind=="ERROR";s.shutdown()
