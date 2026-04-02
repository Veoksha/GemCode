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


def test_tool_context_circulation_disabled_for_gemini2p5_deep_research(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.model = "gemini-2.5-flash"
  cfg.model_mode = "fast"
  cfg.enable_deep_research = True

  agent = build_root_agent(cfg, extra_tools=[])
  # thinking config may or may not be set; but tool_config should be unset.
  if agent.generate_content_config is None:
    return
  assert agent.generate_content_config.tool_config is None


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


def test_tool_context_circulation_never_disables_for_gemini3(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.model = "gemini-3-flash-preview"
  cfg.model_mode = "fast"
  cfg.enable_deep_research = True
  cfg.tool_combination_mode = "never"

  agent = build_root_agent(cfg, extra_tools=[])
  if agent.generate_content_config is None:
    return
  assert agent.generate_content_config.tool_config is None

