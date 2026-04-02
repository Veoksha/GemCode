from gemcode.config import GemCodeConfig
from gemcode.model_routing import pick_effective_model


def test_fast_mode_uses_fast_model(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.model = "gemini-fast"
  cfg.model_mode = "fast"
  cfg.model_quality = "gemini-quality"
  cfg.model_family_mode = "primary"
  assert pick_effective_model(cfg, "anything") == "gemini-fast"


def test_quality_mode_uses_quality_model(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.model = "gemini-fast"
  cfg.model_mode = "quality"
  cfg.model_quality = "gemini-quality"
  cfg.model_family_mode = "primary"
  assert pick_effective_model(cfg, "anything") == "gemini-quality"


def test_auto_picks_quality_for_architecture(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.model = "gemini-fast"
  cfg.model_mode = "auto"
  cfg.model_quality = "gemini-quality"
  cfg.model_family_mode = "primary"

  prompt = "Design an architecture for a multi-agent system with schema migrations and performance trade-offs."
  assert pick_effective_model(cfg, prompt) == "gemini-quality"


def test_auto_picks_fast_for_quick_bugfix(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.model = "gemini-fast"
  cfg.model_mode = "auto"
  cfg.model_family_mode = "primary"

  prompt = "Fix this failing pytest bug quickly."
  assert pick_effective_model(cfg, prompt) == "gemini-fast"


def test_alt_family_fast_mode_uses_alt_fast(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.model = "gemini-3-fast"
  cfg.model_alt = "gemini-2-5-fast"
  cfg.model_mode = "fast"
  cfg.model_family_mode = "alt"
  assert pick_effective_model(cfg, "anything") == "gemini-2-5-fast"


def test_auto_family_prefers_alt_for_simple_prompt(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.model = "gemini-3-fast"
  cfg.model_alt = "gemini-2-5-fast"
  cfg.model_mode = "auto"
  cfg.model_family_mode = "auto"
  prompt = "Fix this failing pytest bug quickly."
  assert pick_effective_model(cfg, prompt) == "gemini-2-5-fast"


def test_deep_research_flag_uses_deep_research_model(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.model = "gemini-fast"
  cfg.model_mode = "fast"
  cfg.model_deep_research = "travel_explore"
  cfg.enable_deep_research = True
  assert (
    pick_effective_model(cfg, "anything about research")
    == "travel_explore"
  )


def test_auto_deep_research_prompt_triggers_deep_research_model(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.model = "gemini-fast"
  cfg.model_mode = "auto"
  cfg.model_deep_research = "travel_explore"
  cfg.model_family_mode = "primary"
  prompt = "Do deep research and provide sources and citations."
  assert pick_effective_model(cfg, prompt) == "travel_explore"

