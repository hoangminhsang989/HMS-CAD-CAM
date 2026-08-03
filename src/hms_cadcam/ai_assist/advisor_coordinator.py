"""Stage 13B gateway over the existing broker; no worker before a valid grant."""
from __future__ import annotations
from dataclasses import dataclass
from hms_cadcam.ai_assist.lifecycle import AiAssistBroker, AiRuntimeState
from hms_cadcam.ai_assist.cutting_supervisor import CuttingWorkerSupervisor

@dataclass(frozen=True,slots=True)
class AdvisorOutcome: status:str; worker_started:bool=False; reason:str|None=None
class CuttingAdvisorCoordinator:
 """Preserves Stage 13A ownership: caller samples resources, then explicitly starts."""
 def __init__(self,broker:AiAssistBroker,supervisor:CuttingWorkerSupervisor,*,capability_enabled:bool,preference_enabled:bool)->None:self._broker=broker;self._supervisor=supervisor;self._capability=capability_enabled;self._preference=preference_enabled
 def request(self)->AdvisorOutcome:
  if not self._capability:return AdvisorOutcome("CAPABILITY_DISABLED")
  if not self._preference:return AdvisorOutcome("ADVISOR_DISABLED")
  state=self._broker.request_task()
  return AdvisorOutcome("WAITING_FOR_RESOURCES" if state.state is not AiRuntimeState.READY else "READY",False)
 def observe_and_start(self,snapshot:object)->AdvisorOutcome:
  if not self._capability or not self._preference:return AdvisorOutcome("AI_DISABLED")
  state=self._broker.observe(snapshot) # ResourceSnapshot validation remains owned by Stage 13A.
  if state.state is AiRuntimeState.WAITING_FOR_RESOURCES:return AdvisorOutcome("WAITING_FOR_RESOURCES")
  if state.state is AiRuntimeState.READY:
   state=self._broker.start_ready_task();return AdvisorOutcome("RUNNING" if state.worker_started else "WORKER_ERROR",state.worker_started)
  return AdvisorOutcome(state.state.value,state.worker_started)
 def cancel(self)->AdvisorOutcome:self._broker.cancel_task();return AdvisorOutcome("CANCELLED",reason="REQUEST_CANCELLED")
 def invalidate(self, reason:str)->AdvisorOutcome:
  """Invalidate owner-bound work without introducing a cross-owner event bus."""
  if not isinstance(reason,str) or not reason: raise ValueError("reason must be non-empty")
  self._broker.cancel_task()
  return AdvisorOutcome("INVALIDATED",reason=reason)
 def shutdown(self, reason:str="APPLICATION_SHUTDOWN")->AdvisorOutcome:
  """Idempotent owner shutdown used directly by an existing production owner."""
  self._broker.shutdown()
  return AdvisorOutcome("OFF",reason=reason)
