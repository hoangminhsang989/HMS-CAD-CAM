"""Panel ownership safety tests; opening does not load a model or worker."""
from hms_cadcam.ai_assist.production_bridge_registry import resolve_production_bridge
from hms_cadcam.ui.cutting_advisor_panel import CuttingAdvisorPanel
from PySide6.QtWidgets import QApplication
def test_unknown_editor_is_rejected_without_runtime_side_effects():
 result=resolve_production_bridge(object());assert result.status=="UNSUPPORTED_EDITOR" and result.bridge is None


def test_panel_close_invalidates_owner_without_starting_runtime():
 QApplication.instance() or QApplication([])
 invalidated=[]
 panel=CuttingAdvisorPanel(translate=lambda key:key)
 panel.bind_owner_invalidator(lambda:invalidated.append("invalid"))
 panel.close()
 assert invalidated==["invalid"]
