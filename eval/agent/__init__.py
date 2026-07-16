"""Agent implementations and registry for WebProber-Bench."""

from .base_agent import APIConfig, BaseAgent, scrub_routing_env


AVAILABLE_AGENTS = {
    "claude_code": ("claude_code", "ClaudeCodeWebTester"),
    "claude_code_gold": ("claude_code_gold", "ClaudeCodeWebTester_Gold"),
    "claude_code_gold_hints": ("claude_code_gold_hints", "ClaudeCodeWebTester_GoldHints"),
}

AGENT_REGISTRY = {}

for name, (module_name, class_name) in AVAILABLE_AGENTS.items():
    try:
        module = __import__(f"{__name__}.{module_name}", fromlist=[class_name])
        AGENT_REGISTRY[name] = getattr(module, class_name)
    except Exception as e:
        print(f"[Warning] Failed to import {class_name} from eval.agent.{module_name}. Error: {e}")



__all__ = [
    "APIConfig",
    "BaseAgent",
    "AGENT_REGISTRY",
    "scrub_routing_env",
]
