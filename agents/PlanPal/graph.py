import logging
from langgraph.graph import StateGraph, END
from agents.PlanPal.state import PlanPalState
from agents.PlanPal.nodes import (
    process_line_item_node,
    summary_response_node
)

logger = logging.getLogger(__name__)

def should_continue(state: PlanPalState) -> str:
    if state.get("error"):
        logger.warning(f"🚨 Error detected in graph state: {state['error']}. Routing to summary.")
        return "summary"
    return "continue"

def build_planpal_graph(checkpointer=None):
    workflow = StateGraph(PlanPalState)

    workflow.add_node("process_line_item", process_line_item_node)
    workflow.add_node("summaryresponse", summary_response_node)

    workflow.set_entry_point("process_line_item")

    workflow.add_conditional_edges(
        "process_line_item",
        should_continue,
        {
            "continue": "summaryresponse",
            "summary": "summaryresponse"
        }
    )

    workflow.add_edge("summaryresponse", END)

    return workflow.compile(checkpointer=checkpointer)
