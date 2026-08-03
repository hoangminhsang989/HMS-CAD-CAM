"""Coverage certification guards against unsupported claims."""
from __future__ import annotations
import json
from pathlib import Path
from hms_cadcam.ai_assist.production_bridge_registry import certified_operation_ids
def test_coverage_matrix_has_honest_partial_and_unsupported_rows():
 data=json.loads((Path(__file__).parents[2]/"docs/STAGE13B_OPERATION_PARAMETER_COVERAGE_MATRIX.json").read_text(encoding="utf-8"));states={row["support_state"] for row in data["entries"]};assert "PARTIAL" in states and "UNSUPPORTED" in states
 supported=tuple(row["operation_id"] for row in data["entries"] if row["support_state"] == "SUPPORTED")
 assert supported == certified_operation_ids()
 assert len(supported) == len(set(supported)) == 3
 for row in data["entries"]:
  assert row["operation_id"]
  if row["support_state"] == "SUPPORTED":
   for key in ("operation_class","subtype_or_strategy","construction_route","session_route","editor_context","bridge_class","validator","draft_setter","normal_apply","zero_persistence_test","exactly_once_test","cancel_test","duplicate_handler_test","selective_apply_test","lifecycle_test","legacy_independence_test"):
    assert row[key]
  else:
   assert row["unsupported_reason"]
