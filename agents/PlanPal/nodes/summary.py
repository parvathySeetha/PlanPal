import logging
import json
from agents.PlanPal.state import PlanPalState

logger = logging.getLogger(__name__)

async def summary_response_node(state: PlanPalState) -> PlanPalState:
    """
    Generate the final summary response for PlanPal Agent.
    """
    logger.info("📝 [PlanPal] Generating summary response...")

    if state.get("error"):
        state["final_response"] = f"PlanPal encountered an error: {state['error']}"
        if not state.get("structured_summary"):
            state["structured_summary"] = {"status": "error", "message": state["error"]}
        return state

    insertion_status = state.get("insertion_status", {})
    if insertion_status.get("success"):
        # The JSON definition is already stored in structured_summary by process_line_item_node
        product = state.get("structured_summary", {}).get("DUMMY_Title__c", "the product")
        state["final_response"] = f"Ready to configure {product}."
    else:
        state["final_response"] = "No configuration was generated."
        if not state.get("structured_summary"):
            state["structured_summary"] = {"status": "no_action"}

    return state
