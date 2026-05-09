import os
from typing import TypedDict
from langgraph.graph import StateGraph, END

class State(TypedDict):
    alert: dict
    message: str

def receive_alert(state: State):
    alert = state["alert"]
    namespace = alert["labels"]["namespace"]
    pod = alert["labels"].get("pod", "unknown")

    return {
        **state,
        "message": f"Received alert for namespace={namespace}, pod={pod}"
    }

graph_builder = StateGraph(State)

graph_builder.add_node("receive_alert", receive_alert)

graph_builder.set_entry_point("receive_alert")
graph_builder.add_edge("receive_alert", END)

graph = graph_builder.compile()

test_alert = {
    "labels": {
        "alertname": "KubePodCrashLooping",
        "namespace": "test-app",
        "pod": "broken-app-123"
    },
    "annotations": {
        "summary": "Pod is crash looping"
    }
}

result = graph.invoke({
    "alert": test_alert,
    "message": ""
})

print(result)