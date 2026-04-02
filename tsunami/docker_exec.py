"""Docker-backed execution sandbox for shell and Python tools."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import threading
from pathlib import Path


DEFAULT_DOCKER_IMAGE = "tsunami-exec:latest"
DEFAULT_CONTAINER_ROOT = "/workspace/tsunami"
DEFAULT_PORTS = (4173, 5173, 8000, 8080, 9876)

_worker_lock = threading.Lock()
_python_worker: subprocess.Popen[str] | None = None
_python_worker_key: tuple[str, str, str] | None = None
_browser_worker: subprocess.Popen[str] | None = None
_browser_worker_key: tuple[str, str] | None = None


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def workspace_root() -> Path:
    return repo_root() / "workspace"


def docker_mode() -> str:
    return os.environ.get("TSUNAMI_DOCKER_EXEC", "auto").strip().lower()


def running_inside_docker() -> bool:
    return os.environ.get("TSUNAMI_IN_DOCKER", "").strip().lower() in ("1", "true", "yes", "on")


def docker_requested() -> bool:
    if running_inside_docker():
        return False
    mode = docker_mode()
    return mode not in ("0", "false", "no", "off", "disabled")


def docker_required() -> bool:
    return docker_mode() in ("1", "true", "yes", "on", "required")


def docker_image() -> str:
    return os.environ.get("TSUNAMI_DOCKER_IMAGE", DEFAULT_DOCKER_IMAGE).strip() or DEFAULT_DOCKER_IMAGE


def container_root() -> str:
    return os.environ.get("TSUNAMI_DOCKER_ROOT", DEFAULT_CONTAINER_ROOT).strip() or DEFAULT_CONTAINER_ROOT


def parse_port_list(value: str | None = None) -> tuple[int, ...]:
    raw = value if value is not None else os.environ.get("TSUNAMI_DOCKER_PORTS", "")
    if not raw.strip():
        return DEFAULT_PORTS

    ports: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        port = int(part)
        if port <= 0 or port > 65535:
            raise ValueError(f"Invalid Docker port: {part}")
        ports.append(port)
    return tuple(dict.fromkeys(ports)) or DEFAULT_PORTS


def container_name() -> str:
    digest = hashlib.sha1(str(repo_root()).encode("utf-8")).hexdigest()[:12]
    return f"tsunami-exec-{digest}"


def host_path_to_container(path: str) -> str:
    root = repo_root().resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = (root / candidate).resolve()
    else:
        candidate = candidate.resolve()

    try:
        rel = candidate.relative_to(root)
        return f"{container_root()}/{rel.as_posix()}".rstrip("/")
    except ValueError:
        return path


def workdir_to_container(workdir: str) -> str:
    if not workdir:
        return container_root()
    return host_path_to_container(os.path.expanduser(workdir))


def docker_available() -> tuple[bool, str]:
    if not docker_requested():
        return False, "docker execution disabled"

    try:
        version = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        return False, "docker CLI not installed"
    except Exception as exc:
        return False, f"docker unavailable: {exc}"

    if version.returncode != 0:
        detail = (version.stderr or version.stdout).strip() or "docker daemon unavailable"
        return False, detail
    return True, ""


def _docker_image_exists(image: str) -> bool:
    result = subprocess.run(
        ["docker", "image", "inspect", image],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.returncode == 0


def _docker_container_running(name: str) -> bool:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", name],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _auto_build_enabled() -> bool:
    return os.environ.get("TSUNAMI_DOCKER_AUTO_BUILD", "1").strip().lower() not in ("0", "false", "no", "off")


def ensure_exec_container() -> tuple[bool, str]:
    available, reason = docker_available()
    if not available:
        return False, reason

    image = docker_image()
    if not _docker_image_exists(image):
        if not _auto_build_enabled():
            return False, f"docker image missing: {image}"
        dockerfile = repo_root() / "docker" / "exec.Dockerfile"
        if not dockerfile.exists():
            return False, f"docker image missing and Dockerfile not found: {dockerfile}"
        build = subprocess.run(
            ["docker", "build", "-t", image, "-f", str(dockerfile), str(repo_root())],
            capture_output=True,
            text=True,
            timeout=1800,
        )
        if build.returncode != 0:
            detail = (build.stderr or build.stdout).strip() or f"failed to build {image}"
            return False, detail

    name = container_name()
    if _docker_container_running(name):
        return True, ""

    subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True, timeout=10)

    workspace = workspace_root().resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    cmd = [
        "docker", "run", "-d", "--rm",
        "--name", name,
        "--add-host", "host.docker.internal:host-gateway",
        "-v", f"{workspace}:{container_root()}/workspace",
        "-w", container_root(),
        "-e", "TSUNAMI_IN_DOCKER=1",
        "-e", "TSUNAMI_DOCKER_EXEC=0",
        "-e", f"TSUNAMI_WORKSPACE={container_root()}/workspace",
    ]
    for port in parse_port_list():
        cmd.extend(["-p", f"{port}:{port}"])
    cmd.extend([
        image,
        "sleep",
        "infinity",
    ])
    run = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if run.returncode != 0:
        detail = (run.stderr or run.stdout).strip() or f"failed to start container {name}"
        return False, detail
    return True, ""


def docker_exec_command(command: str, workdir: str = "") -> list[str]:
    return [
        "docker", "exec", "-i",
        "-w", workdir_to_container(workdir),
        container_name(),
        "bash", "-lc", command,
    ]


async def start_background_shell(command: str, workdir: str = "") -> tuple[asyncio.subprocess.Process | None, str | None]:
    ok, reason = ensure_exec_container()
    if not ok:
        return None, reason
    proc = await asyncio.create_subprocess_exec(
        *docker_exec_command(command, workdir),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    return proc, None


async def run_shell(command: str, workdir: str = "", timeout: int = 120) -> tuple[str, str, int, str | None]:
    ok, reason = ensure_exec_container()
    if not ok:
        return "", "", 0, reason

    proc = await asyncio.create_subprocess_exec(
        *docker_exec_command(command, workdir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise
    return (
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
        proc.returncode or 0,
        None,
    )


_PYTHON_WORKER_CODE = r"""
import io
import json
import os
import sys
import traceback
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

ns = {"__builtins__": __builtins__}

def ensure_defaults():
    if "os" in ns:
        return
    import collections, csv, datetime, json as json_mod, math, re
    ns["os"] = os
    ns["json"] = json_mod
    ns["csv"] = csv
    ns["re"] = re
    ns["math"] = math
    ns["datetime"] = datetime
    ns["collections"] = collections
    ns["Path"] = Path

ensure_defaults()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    code = req.get("code", "")
    cwd = req.get("cwd") or os.getcwd()
    ark_dir = req.get("ark_dir") or os.getcwd()
    prev_cwd = os.getcwd()
    out = io.StringIO()
    err = io.StringIO()
    ns["ARK_DIR"] = ark_dir
    ns["WORKSPACE"] = os.path.join(ark_dir, "workspace")
    ns["DELIVERABLES"] = os.path.join(ark_dir, "workspace", "deliverables")
    ns["CWD"] = cwd
    ns["PROJECT_ROOT"] = cwd
    try:
        os.chdir(cwd)
        with redirect_stdout(out), redirect_stderr(err):
            try:
                result = eval(code, ns)
                if result is not None:
                    print(repr(result))
            except SyntaxError:
                exec(code, ns)
        resp = {"ok": True, "stdout": out.getvalue(), "stderr": err.getvalue()}
    except Exception as exc:
        tb = traceback.format_exc()
        if len(tb) > 500:
            tb = "..." + tb[-500:]
        resp = {"ok": False, "error": f"Error: {exc}\n{tb}"}
    finally:
        try:
            os.chdir(prev_cwd)
        except Exception:
            pass
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()
"""


def _start_python_worker(exec_cwd: str) -> tuple[subprocess.Popen[str] | None, str | None]:
    ok, reason = ensure_exec_container()
    if not ok:
        return None, reason
    proc = subprocess.Popen(
        [
            "docker", "exec", "-i",
            "-w", workdir_to_container(exec_cwd),
            container_name(),
            "python3", "-u", "-c", _PYTHON_WORKER_CODE,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    return proc, None


def _python_worker_key_for(exec_cwd: str) -> tuple[str, str, str]:
    return (container_name(), docker_image(), workdir_to_container(exec_cwd))


def execute_python(code: str, exec_cwd: str, ark_dir: str) -> tuple[bool, str, str | None]:
    global _python_worker, _python_worker_key

    with _worker_lock:
        key = _python_worker_key_for(exec_cwd)
        if (
            _python_worker is None
            or _python_worker.poll() is not None
            or _python_worker_key != key
        ):
            _python_worker, reason = _start_python_worker(exec_cwd)
            _python_worker_key = key if _python_worker is not None else None
            if _python_worker is None:
                return False, "", reason

        assert _python_worker is not None
        if _python_worker.stdin is None or _python_worker.stdout is None:
            return False, "", "docker python worker streams unavailable"

        payload = json.dumps({
            "code": code,
            "cwd": workdir_to_container(exec_cwd),
            "ark_dir": container_root(),
        })
        try:
            _python_worker.stdin.write(payload + "\n")
            _python_worker.stdin.flush()
            line = _python_worker.stdout.readline()
        except Exception as exc:
            return False, "", f"docker python worker error: {exc}"

        if not line:
            stderr = ""
            if _python_worker.stderr is not None:
                try:
                    stderr = _python_worker.stderr.read()
                except Exception:
                    pass
            return False, "", stderr.strip() or "docker python worker exited unexpectedly"

        response = json.loads(line)
        if response.get("ok"):
            output = (response.get("stdout", "") or "").strip()
            stderr = (response.get("stderr", "") or "").strip()
            if stderr:
                output += f"\n[stderr] {stderr}" if output else f"[stderr] {stderr}"
            return True, output, None
        return False, response.get("error", ""), None


_BROWSER_WORKER_CODE = r'''
import json
import os
import traceback
import urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

browser = None
context = None
page = None
playwright_obj = None

def ensure_browser(headless=True, width=1280, height=900):
    global browser, context, page, playwright_obj
    if page is not None:
        page.set_viewport_size({"width": width, "height": height})
        return page
    playwright_obj = sync_playwright().start()
    browser = playwright_obj.chromium.launch(headless=headless)
    context = browser.new_context(
        viewport={"width": width, "height": height},
        user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    )
    page = context.new_page()
    return page

def extract_markdown(p):
    return p.evaluate("""() => {
        const selectors = ['article', 'main', '[role="main"]', '.content', '#content', 'body'];
        for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (el && el.textContent.trim().length > 100) {
                return el.textContent.trim().replace(/\\s+/g, ' ').slice(0, 8000);
            }
        }
        return document.body.textContent.trim().replace(/\\s+/g, ' ').slice(0, 8000);
    }""")

def page_elements(p):
    return p.evaluate("""() => {
        const interactable = document.querySelectorAll(
            'a[href], button, input, textarea, select, [role="button"], [role="link"], [onclick]'
        );
        const results = [];
        let idx = 0;
        for (const el of interactable) {
            if (el.offsetParent === null) continue;
            const tag = el.tagName.toLowerCase();
            const text = (el.textContent || el.value || el.placeholder || el.alt || '').trim().slice(0, 80);
            const href = el.href || '';
            const type = el.type || '';
            let desc = `index[${idx}]:<${tag}`;
            if (type) desc += ` type="${type}"`;
            if (href) desc += ` href="${href.slice(0, 100)}"`;
            desc += `>${text}</${tag}>`;
            results.push(desc);
            idx++;
        }
        return results.join('\\n');
    }""")

def visible_interactables(p):
    return [el for el in p.query_selector_all('a[href], button, input, textarea, select, [role="button"], [role="link"], [onclick]') if el.is_visible()]

def handle(action, payload):
    p = None
    if action != "close":
        p = ensure_browser(payload.get("headless", True), payload.get("width", 1280), payload.get("height", 900))

    if action == "navigate":
        response = p.goto(payload["url"], wait_until="domcontentloaded", timeout=30000)
        wait_for = payload.get("wait_for", "")
        if wait_for:
            p.wait_for_selector(wait_for, timeout=10000)
        return {
            "result": f"Navigated to: {payload['url']}\nStatus: {response.status if response else 'unknown'}\nTitle: {p.title()}\n\n{extract_markdown(p)}"
        }

    if action == "view":
        parts = [f"URL: {p.url}", f"Title: {p.title()}", f"\n--- Content ---\n{extract_markdown(p)[:4000]}"]
        elements = page_elements(p)
        if elements:
            parts.append(f"\n--- Interactive Elements ---\n{elements}")
        return {"result": "\n".join(parts)}

    if action == "click":
        visible = visible_interactables(p)
        idx = payload["index"]
        if idx >= len(visible):
            raise RuntimeError(f"Element index {idx} not found")
        desc = visible[idx].evaluate("(el) => el.tagName + ': ' + (el.textContent || '').trim().slice(0, 80)")
        visible[idx].click()
        p.wait_for_load_state("domcontentloaded", timeout=10000)
        return {"result": f"Clicked: {desc}\nPage now: {p.url} — {p.title()}"}

    if action == "input":
        visible = visible_interactables(p)
        idx = payload["index"]
        if idx >= len(visible):
            raise RuntimeError(f"Element index {idx} not found")
        el = visible[idx]
        tag = el.evaluate("(el) => el.tagName + '[' + (el.type || '') + ']'")
        el.focus()
        try:
            el.fill("")
        except Exception:
            pass
        el.type(payload["text"], delay=20)
        if payload.get("press_enter"):
            el.press("Enter")
        result = f"Typed into {tag}: '{payload['text']}'"
        if payload.get("press_enter"):
            result += " [Enter pressed]"
        return {"result": result}

    if action == "scroll":
        amount = payload.get("amount", 500)
        delta = amount if payload.get("direction", "down") == "down" else -amount
        p.evaluate(f"window.scrollBy(0, {delta})")
        scroll_y = p.evaluate("window.scrollY")
        scroll_max = p.evaluate("document.body.scrollHeight - window.innerHeight")
        return {"result": f"Scrolled {payload.get('direction', 'down')} {amount}px. Position: {scroll_y}/{scroll_max}"}

    if action == "find":
        text = extract_markdown(p)
        keyword = payload["keyword"]
        lower = text.lower()
        target = keyword.lower()
        matches = []
        start = 0
        while True:
            idx = lower.find(target, start)
            if idx == -1:
                break
            context = text[max(0, idx - 100):min(len(text), idx + len(keyword) + 100)]
            matches.append(f"...{context}...")
            start = idx + 1
            if len(matches) >= 10:
                break
        if not matches:
            return {"result": f"'{keyword}' not found on page"}
        return {"result": f"Found {len(matches)} matches for '{keyword}':\n\n" + "\n---\n".join(matches)}

    if action == "console":
        result = p.evaluate(payload["script"])
        return {"result": f"JS result: {json.dumps(result, default=str)[:5000]}"}

    if action == "fill_form":
        visible = visible_interactables(p)
        filled = []
        for field in payload["fields"]:
            idx = field["index"]
            val = field["value"]
            if idx < len(visible):
                el = visible[idx]
                el.fill(val)
                tag = el.evaluate("(el) => el.tagName + '[' + (el.type||'') + ']'")
                filled.append(f"{tag}={val[:50]}")
            else:
                filled.append(f"index {idx}: NOT FOUND")
        return {"result": f"Filled {len(filled)} fields: {'; '.join(filled)}"}

    if action == "press_key":
        p.keyboard.press(payload["key"])
        return {"result": f"Pressed key: {payload['key']}"}

    if action == "select":
        visible = visible_interactables(p)
        idx = payload["index"]
        if idx >= len(visible):
            raise RuntimeError(f"Element index {idx} not found")
        el = visible[idx]
        tag = el.evaluate("(el) => el.tagName")
        if tag.lower() != "select":
            raise RuntimeError(f"Element at index {idx} is <{tag}>, not <select>")
        try:
            el.select_option(value=payload["value"])
        except Exception:
            el.select_option(label=payload["value"])
        return {"result": f"Selected '{payload['value']}' from dropdown at index {idx}"}

    if action == "save_image":
        save_path = Path(payload["save_path"]).expanduser().resolve()
        save_path.parent.mkdir(parents=True, exist_ok=True)
        url = payload.get("url", "screenshot")
        if url == "screenshot":
            p.screenshot(path=str(save_path), full_page=True)
            return {"result": f"Screenshot saved to {save_path}"}
        with urllib.request.urlopen(url, timeout=30) as resp:
            save_path.write_bytes(resp.read())
        return {"result": f"Image saved to {save_path} ({save_path.stat().st_size} bytes)"}

    if action == "upload":
        file_path = Path(payload["file_path"]).expanduser().resolve()
        if not file_path.exists():
            raise RuntimeError(f"File not found: {payload['file_path']}")
        elements = p.query_selector_all('input[type=\"file\"]')
        idx = payload["index"]
        if idx >= len(elements):
            raise RuntimeError(f"File input index {idx} not found. Found {len(elements)} file inputs.")
        elements[idx].set_input_files(str(file_path))
        return {"result": f"Uploaded {file_path.name} to file input {idx}"}

    if action == "screenshot":
        normalized = Path(payload["output_path"]).expanduser().resolve()
        normalized.parent.mkdir(parents=True, exist_ok=True)
        p.goto(payload["url"], wait_until="networkidle", timeout=15000)
        p.wait_for_timeout(1000)
        error_text = p.evaluate("""() => {
            const viteErr = document.querySelector('vite-error-overlay');
            if (viteErr) return viteErr.shadowRoot?.querySelector('.message-body')?.textContent || 'Vite error detected';
            const reactErr = document.querySelector('#react-refresh-overlay');
            if (reactErr) return reactErr.textContent?.substring(0, 500) || 'React error detected';
            const body = document.body?.innerText || '';
            if (body.includes('Failed to resolve import') || body.includes('SyntaxError') || body.includes('Module not found'))
                return body.substring(0, 500);
            return null;
        }""")
        p.screenshot(path=str(normalized), full_page=payload.get("full_page", True))
        return {
            "result": {
                "path": str(normalized),
                "error_text": error_text,
                "size_kb": normalized.stat().st_size // 1024,
            }
        }

    if action == "close":
        global browser, context, page, playwright_obj
        if browser:
            browser.close()
        if playwright_obj:
            playwright_obj.stop()
        browser = None
        context = None
        page = None
        playwright_obj = None
        return {"result": "Browser closed."}

    raise RuntimeError(f"Unknown browser action: {action}")

for line in __import__("sys").stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    try:
        response = handle(req["action"], req.get("payload", {}) or {})
        __import__("sys").stdout.write(json.dumps({"ok": True, **response}) + "\n")
    except Exception as exc:
        tb = traceback.format_exc()
        if len(tb) > 500:
            tb = "..." + tb[-500:]
        __import__("sys").stdout.write(json.dumps({"ok": False, "error": f"{exc}\n{tb}"}) + "\n")
    __import__("sys").stdout.flush()
'''


def _start_browser_worker() -> tuple[subprocess.Popen[str] | None, str | None]:
    ok, reason = ensure_exec_container()
    if not ok:
        return None, reason
    proc = subprocess.Popen(
        [
            "docker", "exec", "-i",
            container_name(),
            "python3", "-u", "-c", _BROWSER_WORKER_CODE,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    return proc, None


def execute_browser(action: str, payload: dict | None = None) -> tuple[bool, object, str | None]:
    global _browser_worker, _browser_worker_key

    with _worker_lock:
        key = (container_name(), docker_image())
        if (
            _browser_worker is None
            or _browser_worker.poll() is not None
            or _browser_worker_key != key
        ):
            _browser_worker, reason = _start_browser_worker()
            _browser_worker_key = key if _browser_worker is not None else None
            if _browser_worker is None:
                return False, "", reason

        assert _browser_worker is not None
        if _browser_worker.stdin is None or _browser_worker.stdout is None:
            return False, "", "docker browser worker streams unavailable"

        effective = dict(payload or {})
        for path_key in ("save_path", "output_path", "file_path"):
            val = effective.get(path_key)
            if isinstance(val, str):
                effective[path_key] = host_path_to_container(val)

        try:
            _browser_worker.stdin.write(json.dumps({"action": action, "payload": effective}) + "\n")
            _browser_worker.stdin.flush()
            line = _browser_worker.stdout.readline()
        except Exception as exc:
            return False, "", f"docker browser worker error: {exc}"

        if not line:
            stderr = ""
            if _browser_worker.stderr is not None:
                try:
                    stderr = _browser_worker.stderr.read()
                except Exception:
                    pass
            return False, "", stderr.strip() or "docker browser worker exited unexpectedly"

        response = json.loads(line)
        if response.get("ok"):
            return True, response.get("result", ""), None
        return False, response.get("error", ""), None
