from google.genai import types

from gemcode.agent import build_root_agent
from gemcode.config import GemCodeConfig


def test_tool_context_circulation_enabled_for_gemini3_deep_research(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.model = "gemini-3-flash-preview"
  cfg.model_mode = "fast"
  cfg.enable_deep_research = True

  agent = build_root_agent(cfg, extra_tools=[])
  assert agent.generate_content_config is not None

  assert isinstance(agent.generate_content_config, types.GenerateContentConfig)
  assert agent.generate_content_config.tool_config is not None
  assert (
    agent.generate_content_config.tool_config.include_server_side_tool_invocations is True
  )


def test_tool_context_circulation_enabled_for_gemini25_mixed_tools(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.model = "gemini-2.5-flash"
  cfg.model_mode = "fast"
  cfg.enable_deep_research = False

  agent = build_root_agent(cfg, extra_tools=[])
  assert agent.generate_content_config is not None
  assert agent.generate_content_config.tool_config is not None
  assert (
    agent.generate_content_config.tool_config.include_server_side_tool_invocations is True
  )


def test_tool_context_circulation_always_enables_for_gemini3(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.model = "gemini-3-flash-preview"
  cfg.model_mode = "fast"
  cfg.enable_deep_research = False
  cfg.tool_combination_mode = "always"

  agent = build_root_agent(cfg, extra_tools=[])
  assert agent.generate_content_config is not None
  assert agent.generate_content_config.tool_config is not None
  assert (
    agent.generate_content_config.tool_config.include_server_side_tool_invocations is True
  )


def test_tool_context_circulation_never_disables_deep_research_only(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.model = "gemini-3-flash-preview"
  cfg.model_mode = "fast"
  cfg.enable_deep_research = True
  cfg.tool_combination_mode = "never"

  agent = build_root_agent(cfg, extra_tools=[])
  assert agent.generate_content_config is not None
  # Built-in + function tool mix still requires the flag even when deep-research is off.
  assert agent.generate_content_config.tool_config is not None
  assert (
    agent.generate_content_config.tool_config.include_server_side_tool_invocations is True
  )
