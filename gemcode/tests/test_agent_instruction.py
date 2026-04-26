from pathlib import Path

from gemcode.agent import build_instruction
from gemcode.config import GemCodeConfig
from gemcode.tool_prompt_manifest import build_tool_manifest


def test_instruction_includes_runtime_facts(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path, model="gemini-2.5-flash")
  text = build_instruction(cfg)
  assert str(tmp_path.resolve()) in text
  assert "gemini-2.5-flash" in text
  assert "GEMCODE_MODEL" in text


def test_instruction_includes_calibration_section(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path, model="gemini-2.5-flash")
  text = build_instruction(cfg)
  assert "Calibration and dynamic routing" in text
  assert "Orchestration" in text


def test_instruction_includes_engineering_discipline_section(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path, model="gemini-2.5-flash")
  text = build_instruction(cfg)
  assert "Engineering discipline (change quality)" in text


def test_instruction_omits_engineering_discipline_when_disabled(tmp_path: Path, monkeypatch) -> None:
  monkeypatch.setenv("GEMCODE_ENGINEERING_DISCIPLINE", "0")
  cfg = GemCodeConfig(project_root=tmp_path, model="gemini-2.5-flash")
  text = build_instruction(cfg)
  assert "Engineering discipline (change quality)" not in text


def test_tool_manifest_includes_engineering_discipline_aligned_block(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path, model="gemini-2.5-flash")
  m = build_tool_manifest(cfg)
  assert m is not None
  assert "Engineering discipline (aligned with main instruction)" in m


def test_tool_manifest_omits_engineering_discipline_when_disabled(tmp_path: Path, monkeypatch) -> None:
  monkeypatch.setenv("GEMCODE_ENGINEERING_DISCIPLINE", "0")
  cfg = GemCodeConfig(project_root=tmp_path, model="gemini-2.5-flash")
  m = build_tool_manifest(cfg)
  assert m is not None
  assert "Engineering discipline (aligned with main instruction)" not in m


def test_tool_manifest_includes_calibration_aligned_block(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path, model="gemini-2.5-flash")
  m = build_tool_manifest(cfg)
  assert m is not None
  assert "Calibration (aligned with main instruction)" in m
  assert "spawn_subtasks" in m


def test_instruction_notes_auto_routing_when_configured(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path, model="gemini-2.5-flash")
  cfg.model_mode = "auto"
  cfg.capability_mode = "auto"
  text = build_instruction(cfg)
  assert "dynamic routing" in text
  assert "model_mode=auto" in text
  assert "capability_mode=auto" in text


def test_instruction_includes_agent_workspace_constitution_when_present(tmp_path: Path) -> None:
  (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)
  (tmp_path / "workspace" / "GOALS.md").write_text("Keep it tight.\n", encoding="utf-8")
  cfg = GemCodeConfig(project_root=tmp_path, model="gemini-2.5-flash")
  text = build_instruction(cfg)
  assert "Agent workspace (local constitution)" in text
  assert "Keep it tight." in text


def test_instruction_omits_agent_workspace_constitution_when_missing(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path, model="gemini-2.5-flash")
  text = build_instruction(cfg)
  assert "Agent workspace (local constitution)" not in text


def test_instruction_mentions_automations_tools(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path, model="gemini-2.5-flash")
  text = build_instruction(cfg)
  assert "Scheduling / automations" in text
  assert "automations_init" in text
  assert "automations_run" in text


def test_instruction_includes_veomem_tool_flow_when_recall_present(tmp_path: Path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path, model="gemini-2.5-flash")
  object.__setattr__(cfg, "_veomem_wakeup_text", "<veomem-context>hello</veomem-context>")
  text = build_instruction(cfg)
  assert "VeoMem recall" in text
  assert "veomem_search(query=...)" in text
  assert "veomem_timeline(id=...)" in text
  assert "veomem_get_observations(ids=...)" in text
