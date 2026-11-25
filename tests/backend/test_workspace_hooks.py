import importlib.util
import io
import json
from pathlib import Path
from types import ModuleType

HOOKS_DIR = Path(__file__).resolve().parents[2] / ".github" / "hooks"


def load_hook(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, HOOKS_DIR / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_session_start_context_reports_installed_app_packages(capsys, monkeypatch):
    workspace_context = load_hook("workspace_context")
    monkeypatch.setattr(workspace_context.sys, "stdin", io.StringIO(""))

    workspace_context.main()

    output = json.loads(capsys.readouterr().out)
    context = output["additionalContext"]
    assert "local app packages present under apps/" in context
    assert "users" in context
    assert "distribution" in context
    assert "systemMessage" not in output


def test_pre_tool_guard_denies_destructive_shell_command():
    pre_tool_guard = load_hook("pre_tool_guard")

    decision = pre_tool_guard.evaluate(
        {
            "toolName": "powershell",
            "toolArgs": {"command": "python manage.py flush --no-input"},
        }
    )

    assert decision["permissionDecision"] == "deny"


def test_pre_tool_guard_emits_copilot_permission_decision(capsys, monkeypatch):
    pre_tool_guard = load_hook("pre_tool_guard")
    event = {
        "toolName": "powershell",
        "toolArgs": {"command": "python manage.py flush"},
    }
    monkeypatch.setattr(pre_tool_guard.sys, "stdin", io.StringIO(json.dumps(event)))

    pre_tool_guard.main()

    output = json.loads(capsys.readouterr().out)
    assert output["permissionDecision"] == "deny"
    assert "hookSpecificOutput" not in output


def test_pre_tool_guard_denies_edits_to_secret_files():
    pre_tool_guard = load_hook("pre_tool_guard")

    decision = pre_tool_guard.evaluate(
        {
            "toolName": "edit_file",
            "toolArgs": {"filePath": r"D:\work\mobofirm\.env.production"},
        }
    )

    assert decision["permissionDecision"] == "deny"


def test_pre_tool_guard_denies_patch_to_hook_configuration():
    pre_tool_guard = load_hook("pre_tool_guard")

    decision = pre_tool_guard.evaluate(
        {
            "toolName": "apply_patch",
            "toolArgs": {
                "patch": "*** Update File: .github/hooks/hooks.json\n+remove guard"
            },
        }
    )

    assert decision["permissionDecision"] == "deny"


def test_pre_tool_guard_leaves_non_destructive_tool_call_unchanged():
    pre_tool_guard = load_hook("pre_tool_guard")

    decision = pre_tool_guard.evaluate(
        {
            "toolName": "powershell",
            "toolArgs": {"command": "git status --short"},
        }
    )

    assert decision == {}


def test_post_edit_lint_extracts_nested_file_paths():
    post_edit_lint = load_hook("post_edit_lint")

    paths = post_edit_lint.extract_paths(
        {"files": [{"filePath": "apps/users/views.py"}, {"path": "tests/test_x.py"}]}
    )

    assert paths == ["apps/users/views.py", "tests/test_x.py"]


def test_post_edit_lint_extracts_patch_targets():
    post_edit_lint = load_hook("post_edit_lint")

    paths = post_edit_lint.extract_paths(
        {"patch": "*** Update File: apps/users/views.py\n+changed"}
    )

    assert paths == ["apps/users/views.py"]
