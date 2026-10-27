from modules.agent import run_agent

goal = (
    "Investigate migration delays, cross-check risks against"
    "milestone dates and architecture documents, identify evidence"
    "gaps, and recommend management actions"
)

result = run_agent(goal)

print(result["report"])

print(result["tool_log"])