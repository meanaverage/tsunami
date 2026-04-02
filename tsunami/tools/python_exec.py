"""Persistent Python interpreter — CodeAct paradigm.

Instead of fixed tool calls, the agent writes executable Python code.
The interpreter persists across calls — variables, imports, and state
survive. This collapses 5-10 sequential tool calls into 1.

This is the single most impactful Manus feature.
"""

from __future__ import annotations

import asyncio
import builtins
import io
import logging
import os
import re
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from ..docker_exec import docker_required, docker_requested, execute_python as execute_python_in_docker
from .base import BaseTool, ToolResult

log = logging.getLogger("tsunami.python_exec")

# Persistent namespace shared across calls
_namespace = {}
_PROTECTED_BUILTINS = (
    "open", "print", "len", "enumerate", "range", "min", "max", "sum",
    "list", "dict", "set", "tuple", "int", "float", "str", "zip",
    "sorted", "any", "all", "isinstance", "repr",
)


def _looks_like_non_python_source(code: str) -> bool:
    snippet = code.strip()
    if not snippet:
        return False

    tsx_markers = (
        'import {',
        'from "./',
        'from "../',
        "export default function",
        "interface ",
        "type ",
        "return (",
        "</div>",
        "<section",
        "<main",
        "<div",
        "className=",
        "onClick=",
    )
    score = sum(1 for marker in tsx_markers if marker in snippet)
    return score >= 2


def _looks_like_frontend_file_write(code: str) -> bool:
    normalized = code.replace("\\\\", "/")
    frontend_suffixes = (".tsx", ".ts", ".css", ".jsx", ".js")
    target_pattern = r'["\'][^"\']+\.(?:tsx|ts|css|jsx|js)["\']'

    write_patterns = (
        r"open\(\s*" + target_pattern + r"\s*,\s*['\"](?:w|a|x)",
        r"Path\(\s*" + target_pattern + r"\s*\)\.write_text\(",
        r"Path\(\s*" + target_pattern + r"\s*\)\.write_bytes\(",
    )
    if any(re.search(pattern, normalized) for pattern in write_patterns):
        return True

    return any(suffix in normalized for suffix in frontend_suffixes) and any(
        marker in normalized
        for marker in (".write_text(", ".write_bytes(", "open(", "with open(")
    )


def _error_hint(exc: Exception, code: str) -> str:
    message = str(exc)
    stripped = code.strip()
    if isinstance(exc, ValueError) and "too many values to unpack" in message:
        return (
            "Hint: one of your loops is unpacking the wrong tuple shape. "
            "If you append 3-tuples like (name, label, size), iterate with three variables "
            "or change the tuple to two values."
        )
    if isinstance(exc, TypeError) and "listdir() takes at most 1 argument" in message:
        return (
            "Hint: os.listdir accepts a single path argument. "
            "Do not pass extra labels or metadata into listdir(). "
            "If you are inspecting scaffold files repeatedly, stop probing and use file_write or file_edit "
            "to replace src/App.tsx with the actual page composition."
        )
    if isinstance(exc, TypeError) and "object is not callable" in message:
        return (
            "Hint: you likely shadowed a Python builtin like open(), len(), or print() in the persistent interpreter. "
            "Avoid rebinding builtin names, and prefer file_write or file_edit for TSX/CSS work."
        )
    if isinstance(exc, SyntaxError) and stripped.startswith("import "):
        return "Hint: this is multi-line Python, so exec() is the correct path; inspect the later traceback frame for the real runtime error."
    return ""


def _execution_cwd() -> str:
    """Use the active project root when available, otherwise fall back to repo root."""
    try:
        from .plan import get_agent_state
        state = get_agent_state()
        root = getattr(state, "active_project_root", "") if state is not None else ""
        if root and os.path.isdir(root):
            return root
    except Exception:
        pass
    return str(Path(__file__).parent.parent.parent)


def _normalize_project_prefixed_code(code: str, exec_cwd: str) -> str:
    """Rewrite workspace-prefixed paths to the active project's absolute path.

    The model often emits paths like ./workspace/deliverables/<project>/src/App.tsx
    even though python_exec runs from that project's root. Rewriting these to the
    absolute project root makes the code robust even if cwd drift occurs.
    """
    try:
        project_root = Path(exec_cwd).resolve()
        from .plan import get_agent_state
        state = get_agent_state()
        active_project = getattr(state, "active_project", "") if state is not None else ""
    except Exception:
        return code

    project_name = active_project
    try:
        parts = project_root.parts
        if "deliverables" in parts:
            deliverables_index = parts.index("deliverables")
            if deliverables_index + 1 < len(parts):
                project_name = parts[deliverables_index + 1]
    except Exception:
        pass

    if not project_name:
        return code

    repo_root = Path(__file__).parent.parent.parent.resolve()
    repo_root_posix = repo_root.as_posix()
    project_root_posix = project_root.as_posix()

    prefixes = [
        f"./workspace/deliverables/{project_name}/",
        f"workspace/deliverables/{project_name}/",
        f"/workspace/deliverables/{project_name}/",
        f"./workspace/tsunami/workspace/deliverables/{project_name}/",
        f"/workspace/tsunami/workspace/deliverables/{project_name}/",
        f"{repo_root_posix}/workspace/deliverables/{project_name}/",
        f"{project_root_posix}/",
    ]

    normalized = code
    for prefix in prefixes:
        normalized = normalized.replace(prefix, f"{project_root_posix}/")

    normalized = re.sub(
        r"(?<![\w/])(?:\.?/)?workspace/deliverables/[^/\s'\"`]+/",
        f"{project_root_posix}/",
        normalized,
    )
    normalized = re.sub(
        r"(?<![\w.])/workspace/deliverables/[^/\s'\"`]+/",
        f"{project_root_posix}/",
        normalized,
    )
    normalized = re.sub(
        r"(?<![\w/])(?:\./)?workspace/tsunami/workspace/deliverables/[^/\s'\"`]+/",
        f"{project_root_posix}/",
        normalized,
    )
    normalized = re.sub(
        rf"{re.escape(repo_root_posix)}/workspace/deliverables/[^/\s'\"`]+/",
        f"{project_root_posix}/",
        normalized,
    )

    normalized = re.sub(r"(?<![\w/])\./tsunami/", f"{repo_root_posix}/tsunami/", normalized)
    normalized = re.sub(r"(?<![\w/])tsunami/", f"{repo_root_posix}/tsunami/", normalized)
    normalized = re.sub(r"(?<![\w/])\./toolboxes/", f"{repo_root_posix}/toolboxes/", normalized)
    normalized = re.sub(r"(?<![\w/])toolboxes/", f"{repo_root_posix}/toolboxes/", normalized)
    normalized = re.sub(r"(?<![\w/])\./README\.md", f"{repo_root_posix}/README.md", normalized)
    normalized = re.sub(r"(?<![\w/])README\.md", f"{repo_root_posix}/README.md", normalized)

    duplicate_prefixes = [
        f"{repo_root_posix}/{project_root_posix}",
        f"{repo_root_posix}//{project_root_posix.lstrip('/')}",
    ]
    for duplicate in duplicate_prefixes:
        normalized = normalized.replace(duplicate, project_root_posix)

    return normalized


def _restore_protected_builtins() -> None:
    _namespace["__builtins__"] = builtins.__dict__
    for name in _PROTECTED_BUILTINS:
        _namespace[name] = getattr(builtins, name)


class PythonExec(BaseTool):
    """Execute Python code in a persistent interpreter."""

    name = "python_exec"
    description = "Run Python code in a persistent interpreter. State survives across calls. print() for output."

    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute. Use print() for output.",
                },
            },
            "required": ["code"],
        }

    async def execute(self, code: str = "", **kwargs) -> ToolResult:
        if not code.strip():
            return ToolResult("No code provided", is_error=True)

        if _looks_like_non_python_source(code):
            return ToolResult(
                "This looks like TS/JSX source code, not Python. "
                "Use file_write or file_edit to update src/*.tsx, src/*.ts, or CSS files instead of python_exec.",
                is_error=True,
            )

        if _looks_like_frontend_file_write(code):
            return ToolResult(
                "python_exec is not for writing frontend source files. "
                "Use file_write to create full src/*.tsx or CSS files, and use file_edit for small targeted changes. "
                "Reserve python_exec for data processing, filesystem inspection, and calculations.",
                is_error=True,
            )

        blocked = ["shutil.rmtree", "os.remove", "os.unlink", "subprocess.call('rm"]
        for blocked_item in blocked:
            if blocked_item in code:
                return ToolResult(f"BLOCKED: {blocked_item} is not allowed in python_exec", is_error=True)

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        if "os" not in _namespace:
            import collections
            import csv
            import datetime
            import importlib
            import json
            import math

            _namespace["os"] = os
            _namespace["json"] = json
            _namespace["csv"] = csv
            _namespace["re"] = re
            _namespace["math"] = math
            _namespace["datetime"] = datetime
            _namespace["collections"] = collections
            _namespace["importlib"] = importlib
            _namespace["Path"] = Path
            _namespace["sys"] = sys

        _restore_protected_builtins()

        ark_dir = str(Path(__file__).parent.parent.parent)
        exec_cwd = _execution_cwd()
        code = _normalize_project_prefixed_code(code, exec_cwd)

        if docker_requested():
            ok, output, reason = await asyncio.to_thread(execute_python_in_docker, code, exec_cwd, ark_dir)
            if ok:
                output = output.strip() or "(no output — code executed successfully)"
                if len(output) > 8000:
                    output = output[:8000] + "\n... [TRUNCATED]"
                return ToolResult(f"{output}\n[exec mode: docker]".rstrip())
            if docker_required():
                return ToolResult(f"Docker execution required but unavailable: {reason or output}", is_error=True)

        prev_cwd = os.getcwd()
        os.chdir(exec_cwd)
        _namespace["ARK_DIR"] = ark_dir
        _namespace["WORKSPACE"] = os.path.join(ark_dir, "workspace")
        _namespace["DELIVERABLES"] = os.path.join(ark_dir, "workspace", "deliverables")
        _namespace["CWD"] = exec_cwd
        _namespace["PROJECT_ROOT"] = exec_cwd

        try:
            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                try:
                    result = eval(code, _namespace)
                    if result is not None:
                        print(repr(result), file=stdout_buf)
                except SyntaxError:
                    exec(code, _namespace)

            stdout = stdout_buf.getvalue().strip()
            stderr = stderr_buf.getvalue().strip()

            output = stdout
            if stderr:
                output += f"\n[stderr] {stderr}" if output else f"[stderr] {stderr}"

            if not output:
                output = "(no output — code executed successfully)"

            if len(output) > 8000:
                output = output[:8000] + "\n... [TRUNCATED]"

            return ToolResult(output)

        except Exception as exc:
            tb = traceback.format_exc()
            if len(tb) > 500:
                tb = "..." + tb[-500:]
            hint = _error_hint(exc, code)
            extra = f"\n{hint}" if hint else ""
            return ToolResult(f"Error: {exc}{extra}\n{tb}", is_error=True)
        finally:
            try:
                os.chdir(prev_cwd)
            except Exception:
                pass
