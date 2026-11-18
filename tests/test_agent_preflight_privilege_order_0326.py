"""Regression: read the agent's 0700 directory only with elevated rights."""
from pathlib import Path

def test_preflight_checks_agent_identity_only_after_privilege_switch():
    s=(Path(__file__).resolve().parents[1]/"app/agent_updates.py").read_text()
    start=s.index('if agent["platform"]=="linux":',s.index("def preflight("))
    block=s[start:s.index('result["status"]="ok"',start)]
    assert "'else exec sudo -n sh -c '" in block
    assert "'exec sh -c '" in block
    assert 'python3 -c ' in block
    assert 'agent_identity' in block
    assert 'c.get(\'agent_id\')' in block
    assert 'test -f /opt/origamitasks-agent/config.json &&' not in block
