"""Bee — a lightweight agent worker powered by the 2B model.

A bee is a mini agent loop: it receives a task, has access to tools
(file_read, shell_exec, match_grep, etc.), and runs autonomously
until it completes or hits a turn limit.

Unlike the queen (27B full agent), bees are:
- Fast (~100 tok/s on 2B)
- Cheap (small context, no vision)
- Disposable (no session persistence)
- Parallel (multiple bees run simultaneously)

The queen dispatches bees for:
- Reading/analyzing multiple files
- Running independent shell commands
- Searching across a codebase
- Any batch of homogeneous tasks
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

log = logging.getLogger("tsunami.bee")

BEE_ENDPOINT = os.environ.get("TSUNAMI_BEE_ENDPOINT", "http://localhost:8092")
MAX_TURNS = 10  # safety cap per bee
BEE_TIMEOUT = 30  # seconds per LLM call


@dataclass
class BeeResult:
    """Result from a bee's work."""
    task: str
    success: bool
    output: str
    tool_calls: int = 0
    turns: int = 0
    elapsed_ms: float = 0
    error: str = ""


# Tools available to bees (subset of queen's tools)
BEE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "file_read",
            "description": "Read a file's content",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "offset": {"type": "integer", "description": "Start line", "default": 0},
                    "limit": {"type": "integer", "description": "Max lines", "default": 200},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_exec",
            "description": "Run a shell command",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Command to run"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "match_grep",
            "description": "Search file contents by regex",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern"},
                    "directory": {"type": "string", "description": "Where to search", "default": "."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "Report your final answer",
            "parameters": {
                "type": "object",
                "properties": {
                    "result": {"type": "string", "description": "Your findings/answer"},
                },
                "required": ["result"],
            },
        },
    },
]


async def _execute_bee_tool(name: str, args: dict, workdir: str) -> str:
    """Execute a tool on behalf of a bee. Runs locally (not via LLM)."""
    if name == "file_read":
        path = args.get("path", "")
        p = Path(path) if Path(path).is_absolute() else Path(workdir) / path
        if not p.exists():
            return f"File not found: {path}"
        text = p.read_text(errors="replace")
        lines = text.splitlines()
        offset = args.get("offset", 0)
        limit = args.get("limit", 200)
        selected = lines[offset:offset + limit]
        return "\n".join(selected)[:4000]

    elif name == "shell_exec":
        cmd = args.get("command", "")
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, cwd=workdir,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            out = stdout.decode(errors="replace")[:4000]
            if stderr:
                out += f"\n[stderr] {stderr.decode(errors='replace')[:1000]}"
            return out
        except asyncio.TimeoutError:
            return "Command timed out after 30s"
        except Exception as e:
            return f"Error: {e}"

    elif name == "match_grep":
        pattern = args.get("pattern", "")
        directory = args.get("directory", workdir)
        if not Path(directory).is_absolute():
            directory = str(Path(workdir) / directory)
        try:
            proc = await asyncio.create_subprocess_exec(
                "grep", "-rn", "--include=*.py", "--include=*.ts",
                "--include=*.js", "--include=*.md", pattern, directory,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            return stdout.decode(errors="replace")[:4000] or "No matches"
        except Exception as e:
            return f"Grep error: {e}"

    elif name == "done":
        return args.get("result", "")

    return f"Unknown tool: {name}"


async def run_bee(
    task: str,
    workdir: str = ".",
    endpoint: str = BEE_ENDPOINT,
    max_turns: int = MAX_TURNS,
    system_prompt: str = "",
) -> BeeResult:
    """Run a single bee agent loop.

    The bee gets a task, can use tools, and runs until it calls
    'done' or hits the turn limit.
    """
    start = time.time()

    if not system_prompt:
        system_prompt = (
            "You are a focused worker agent. Complete the task using the tools available. "
            "When you have the answer, call the 'done' tool with your result. "
            "Be concise. Do not explain your process — just deliver the result."
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]

    tool_calls = 0
    turns = 0

    async with httpx.AsyncClient(timeout=BEE_TIMEOUT) as client:
        for turn in range(max_turns):
            turns = turn + 1

            try:
                resp = await client.post(
                    f"{endpoint}/v1/chat/completions",
                    json={
                        "model": "qwen",
                        "messages": messages,
                        "tools": BEE_TOOLS,
                        "tool_choice": "auto",
                        "max_tokens": 1024,
                        "temperature": 0.3,
                    },
                    headers={"Authorization": "Bearer not-needed"},
                )

                if resp.status_code != 200:
                    return BeeResult(
                        task=task, success=False, output="",
                        error=f"HTTP {resp.status_code}",
                        turns=turns, elapsed_ms=(time.time() - start) * 1000,
                    )

                data = resp.json()
                choice = data["choices"][0]
                msg = choice["message"]

                # Check for tool calls
                if msg.get("tool_calls"):
                    tc = msg["tool_calls"][0]
                    func = tc["function"]
                    name = func["name"]
                    raw_args = func.get("arguments", "{}")
                    if isinstance(raw_args, dict):
                        args = raw_args
                    elif isinstance(raw_args, str):
                        try:
                            args = json.loads(raw_args)
                        except json.JSONDecodeError:
                            args = {}
                    else:
                        args = {}

                    tool_calls += 1

                    # Done tool = terminate
                    if name == "done":
                        return BeeResult(
                            task=task, success=True,
                            output=args.get("result", ""),
                            tool_calls=tool_calls, turns=turns,
                            elapsed_ms=(time.time() - start) * 1000,
                        )

                    # Execute tool
                    result = await _execute_bee_tool(name, args, workdir)

                    # Add to conversation
                    messages.append({"role": "assistant", "content": msg.get("content", ""), "tool_calls": msg["tool_calls"]})
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result,
                    })

                else:
                    # Text response — treat as final answer
                    content = msg.get("content", "").strip()
                    if content:
                        return BeeResult(
                            task=task, success=True, output=content,
                            tool_calls=tool_calls, turns=turns,
                            elapsed_ms=(time.time() - start) * 1000,
                        )

            except httpx.TimeoutException:
                return BeeResult(
                    task=task, success=False, output="",
                    error="Timeout", turns=turns,
                    elapsed_ms=(time.time() - start) * 1000,
                )
            except Exception as e:
                return BeeResult(
                    task=task, success=False, output="",
                    error=str(e)[:200], turns=turns,
                    elapsed_ms=(time.time() - start) * 1000,
                )

    return BeeResult(
        task=task, success=False, output="",
        error=f"Hit turn limit ({max_turns})",
        turns=turns, tool_calls=tool_calls,
        elapsed_ms=(time.time() - start) * 1000,
    )


async def run_swarm(
    tasks: list[str],
    workdir: str = ".",
    max_concurrent: int = 4,
    endpoint: str = BEE_ENDPOINT,
    system_prompt: str = "",
) -> list[BeeResult]:
    """Run multiple bees in parallel with concurrency control.

    The queen calls this to dispatch work to the hive.
    """
    sem = asyncio.Semaphore(max_concurrent)
    start = time.time()

    async def _run(task: str) -> BeeResult:
        async with sem:
            return await run_bee(task, workdir, endpoint, system_prompt=system_prompt)

    results = await asyncio.gather(*[_run(t) for t in tasks])
    elapsed = (time.time() - start) * 1000

    succeeded = sum(1 for r in results if r.success)
    total_tool_calls = sum(r.tool_calls for r in results)
    log.info(
        f"Swarm complete: {succeeded}/{len(results)} succeeded, "
        f"{total_tool_calls} tool calls, {elapsed:.0f}ms"
    )

    return list(results)


def format_swarm_results(results: list[BeeResult]) -> str:
    """Format swarm results for the queen to consume."""
    lines = [f"swarm: {len(results)} bees dispatched"]
    for i, r in enumerate(results):
        status = "ok" if r.success else "FAIL"
        lines.append(f"\n[bee {i}] {status} ({r.turns} turns, {r.tool_calls} tools, {r.elapsed_ms:.0f}ms)")
        if r.output:
            lines.append(r.output[:500])
        if r.error:
            lines.append(f"  error: {r.error}")
    return "\n".join(lines)
