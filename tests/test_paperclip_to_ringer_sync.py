import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "paperclip_to_ringer_sync.py"
spec = importlib.util.spec_from_file_location("paperclip_to_ringer_sync", MODULE_PATH)
assert spec is not None and spec.loader is not None
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


def test_critical_blocked_human_action_ranks_before_high_todo():
    issues = [
        {"identifier": "JAC-1", "title": "normal", "status": "todo", "priority": "high"},
        {"identifier": "JAC-2", "title": "authorization needed", "status": "blocked", "priority": "critical"},
    ]

    ranked = sync.rank_actionable(issues)

    assert [issue["identifier"] for issue in ranked] == ["JAC-2", "JAC-1"]


def test_blocked_human_action_is_marked_in_registry_projection():
    issue = {
        "identifier": "JAC-2",
        "title": "authorization needed",
        "status": "blocked",
        "priority": "critical",
        "description": "Mirror of Beads hermes-4i2. Human authorization required.",
        "assigneeAgentId": "agent-1",
        "blockerAttention": {"state": "needs_attention", "reason": "attention_required"},
    }

    projected = sync.project_actionable_issue(issue, {})

    assert projected["needs_human_action"] is True
    assert projected["attention_state"] == "needs_attention"
    assert projected["assignee_agent_id"] == "agent-1"
