from gemcode.capability_routing import apply_capability_routing
from gemcode.config import GemCodeConfig
from gemcode.model_routing import pick_effective_model


def test_research_mode_enables_deep_research(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.capability_mode = "research"
  cfg.enable_deep_research = False
  cfg.enable_embeddings = False
  cfg.enable_computer_use = False
  apply_capability_routing(cfg, "anything", context="prompt")
  assert cfg.enable_deep_research is True
  assert cfg.enable_embeddings is False
  assert cfg.enable_computer_use is False


def test_embeddings_mode_enables_embeddings(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.capability_mode = "embeddings"
  cfg.enable_deep_research = False
  cfg.enable_embeddings = False
  cfg.enable_computer_use = False
  apply_capability_routing(cfg, "anything", context="prompt")
  assert cfg.enable_embeddings is True
  assert cfg.enable_deep_research is False
  assert cfg.enable_computer_use is False


def test_computer_mode_enables_computer_use(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.capability_mode = "computer"
  cfg.enable_deep_research = False
  cfg.enable_embeddings = False
  cfg.enable_computer_use = False
  apply_capability_routing(cfg, "anything", context="prompt")
  assert cfg.enable_computer_use is True
  assert cfg.enable_deep_research is False
  assert cfg.enable_embeddings is False


def test_auto_mode_triggers_on_research_keywords(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.capability_mode = "auto"
  cfg.enable_deep_research = False
  cfg.enable_embeddings = False
  cfg.enable_computer_use = False
  apply_capability_routing(cfg, "Please provide sources and citations.", context="prompt")
  assert cfg.enable_deep_research is True


def test_auto_mode_triggers_on_computer_keywords(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.capability_mode = "auto"
  cfg.enable_deep_research = False
  cfg.enable_embeddings = False
  cfg.enable_computer_use = False
  apply_capability_routing(cfg, "Open the browser and click the login button.", context="prompt")
  # Computer-use is intentionally NEVER auto-enabled from prompt heuristics.
  assert cfg.enable_computer_use is False


def test_pick_effective_model_computer_use_override(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.model = "base-model"
  cfg.model_mode = "fast"
  cfg.model_family_mode = "primary"
  cfg.model_computer_use = "computer-model"

  cfg.enable_deep_research = False
  cfg.enable_audio = False
  cfg.enable_computer_use = True
  cfg.model_overridden = False

  assert pick_effective_model(cfg, "anything") == "computer-model"


def test_pick_effective_model_deep_research_precedence(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.model = "base-model"
  cfg.model_mode = "fast"
  cfg.model_family_mode = "primary"
  cfg.model_computer_use = "computer-model"
  cfg.model_deep_research = "deep-research-model"

  cfg.enable_audio = False
  cfg.enable_computer_use = True
  cfg.enable_deep_research = True
  cfg.model_overridden = False

  assert pick_effective_model(cfg, "anything") == "deep-research-model"


def test_pick_effective_model_honors_explicit_model(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.model = "explicit-model"
  cfg.model_mode = "fast"
  cfg.model_family_mode = "primary"
  cfg.model_computer_use = "computer-model"
  cfg.enable_computer_use = True
  cfg.enable_deep_research = True
  cfg.model_overridden = True

  assert pick_effective_model(cfg, "anything") == "explicit-model"

