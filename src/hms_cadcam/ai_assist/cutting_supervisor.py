"""Real-process supervisor for the Stage 13B stdlib JSONL worker."""
from __future__ import annotations
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import time
from typing import Any
from hms_cadcam.ai_assist.cutting_worker import PROTOCOL
from hms_cadcam.ai_assist.policy import AiTier
from hms_cadcam.ai_assist.supervisor import WorkerStartResult

@dataclass(frozen=True, slots=True)
class WorkerMessage: kind: str; correlation_id: str; payload: Any

class CuttingWorkerSupervisor:
    """Own exactly one child, using no shell and closing every pipe on release."""
    def __init__(self, python: Path, worker: Path, *, startup_timeout_seconds: float=5.0, request_timeout_seconds: float=5.0) -> None:
        self._python=Path(python); self._worker=Path(worker); self._startup=startup_timeout_seconds; self._request=request_timeout_seconds; self._process: subprocess.Popen[str]|None=None
    @property
    def has_worker(self)->bool: return self._process is not None and self._process.poll() is None
    @property
    def pid(self)->int|None: return None if self._process is None else self._process.pid
    def start(self, tier: AiTier) -> WorkerStartResult:
        if self.has_worker: return WorkerStartResult(False,reason_code="WORKER_ALREADY_OWNED")
        if tier is not AiTier.LITE: return WorkerStartResult(False,reason_code="CPU_LITE_ONLY")
        source_root=self._worker.parents[2]
        self._process=subprocess.Popen([str(self._python),"-m","hms_cadcam.ai_assist.cutting_worker"],cwd=str(source_root),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding="utf-8",bufsize=1,shell=False)
        try:
            hello=self._read(self._startup); ready=self._read(self._startup)
            if hello.kind!="HELLO" or hello.payload!={"protocol":PROTOCOL}: raise RuntimeError("WORKER_HELLO_INVALID")
            if ready.kind!="READY" or not isinstance(ready.payload,dict) or ready.payload.get("supported_families")!=["milling","drilling","turning"]: raise RuntimeError("WORKER_READY_INVALID")
            return WorkerStartResult(True,"stage13b-jsonl",self._process.pid)
        except (OSError,RuntimeError,ValueError): self.release(graceful_timeout_seconds=0.1); return WorkerStartResult(False,reason_code="WORKER_START_FAILED")
    def recommend(self, correlation_id: str, payload: dict[str, Any]) -> WorkerMessage:
        if not self.has_worker: raise RuntimeError("WORKER_NOT_RUNNING")
        self._send("RECOMMEND_REQUEST",correlation_id,payload); message=self._read(self._request)
        if message.kind not in {"RECOMMEND_RESULT","ERROR"} or message.correlation_id != correlation_id: raise RuntimeError("WORKER_RESPONSE_INVALID")
        return message
    def cancel(self)->None:
        if self.has_worker: self._send("CANCEL","",None); self._read(self._request)
    def release(self, *, graceful_timeout_seconds: float)->None:
        process=self._process
        if process is None: return
        try:
            if process.poll() is None:
                self._send("SHUTDOWN","",None); process.wait(timeout=graceful_timeout_seconds)
        except (OSError,subprocess.TimeoutExpired):
            process.terminate()
            try: process.wait(timeout=graceful_timeout_seconds)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
        finally:
            for stream in (process.stdin,process.stdout,process.stderr):
                if stream is not None: stream.close()
            self._process=None
    def shutdown(self)->None: self.release(graceful_timeout_seconds=1.0)
    def _send(self, kind:str, correlation_id:str, payload:Any)->None:
        assert self._process is not None and self._process.stdin is not None
        self._process.stdin.write(json.dumps({"protocol":PROTOCOL,"type":kind,"correlation_id":correlation_id,"payload":payload},separators=(",",":"))+"\n"); self._process.stdin.flush()
    def _read(self, timeout:float)->WorkerMessage:
        assert self._process is not None and self._process.stdout is not None
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            line=self._process.stdout.readline()
            if line:
                value=json.loads(line)
                if not isinstance(value,dict) or value.get("protocol")!=PROTOCOL: raise RuntimeError("PROTOCOL_MISMATCH")
                return WorkerMessage(str(value.get("type")),str(value.get("correlation_id","")),value.get("payload"))
            if self._process.poll() is not None: raise RuntimeError("WORKER_EXITED")
        raise RuntimeError("WORKER_TIMEOUT")
