"""Swarm analyze — batch-read files and extract patterns via parallel 2B workers.

When given a directory of files and a question, dispatches all files
across N workers, extracts answers, and synthesizes.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from .base import BaseTool, ToolResult

log = logging.getLogger("tsunami.swarm_analyze")

MAX_WORKERS = int(os.environ.get("TSUNAMI_MAX_WORKERS", "16"))
BEE_ENDPOINT = os.environ.get("TSUNAMI_BEE_ENDPOINT", "http://localhost:8092")


class SwarmAnalyze(BaseTool):
    """Read many files in parallel and extract patterns."""

    name = "swarm_analyze"
    description = (
        "Read ALL files in a directory using parallel workers and answer a question about them. "
        "Use for analyzing 20+ files. Workers read in parallel, results are synthesized. "
        "Much faster than reading files one at a time."
    )

    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "directory": {"type": "string", "description": "Directory containing files to analyze"},
                "question": {"type": "string", "description": "What to extract from each file"},
                "pattern": {"type": "string", "description": "Glob pattern for files", "default": "*.md"},
                "output_path": {"type": "string", "description": "Where to save results", "default": ""},
            },
            "required": ["directory", "question"],
        }

    async def execute(self, directory: str = "", question: str = "",
                      pattern: str = "*.md", output_path: str = "", **kwargs) -> ToolResult:
        if not directory or not question:
            return ToolResult("directory and question required", is_error=True)

        # Resolve directory — handle absolute, relative, and common mistakes
        root = Path(directory).expanduser().resolve()
        if not root.exists():
            # Strip leading /workspace/ → workspace/ (common 2B mistake)
            stripped = directory.lstrip("/")
            root = Path(self.config.workspace_dir).parent / stripped
        if not root.exists():
            root = Path(self.config.workspace_dir).parent / directory.replace("/workspace/", "workspace/")
        if not root.exists():
            return ToolResult(f"Directory not found: {directory}. Try: workspace/deliverables/...", is_error=True)

        files = sorted(root.glob(pattern))
        if not files:
            return ToolResult(f"No {pattern} files in {directory}", is_error=True)

        log.info(f"Swarm analyzing {len(files)} files with {MAX_WORKERS} workers")

        import httpx

        async def process_file(session, filepath):
            try:
                text = filepath.read_text()
                # Take the ending — that's where conclusions/failures are
                chunk = text[-2000:] if len(text) > 2000 else text

                resp = await session.post(
                    f"{BEE_ENDPOINT}/v1/chat/completions",
                    json={
                        "model": "qwen",
                        "messages": [
                            {"role": "system", "content": f"Answer in ONE sentence: {question}"},
                            {"role": "user", "content": chunk},
                        ],
                        "max_tokens": 150,
                    },
                    headers={"Authorization": "Bearer not-needed"},
                    timeout=30,
                )
                if resp.status_code == 200:
                    return filepath.name, resp.json()["choices"][0]["message"]["content"].strip()
                return filepath.name, f"HTTP {resp.status_code}"
            except Exception as e:
                return filepath.name, f"error: {str(e)[:50]}"

        # Process in batches
        results = []
        async with httpx.AsyncClient(timeout=60) as session:
            for i in range(0, len(files), MAX_WORKERS):
                batch = files[i:i + MAX_WORKERS]
                batch_results = await asyncio.gather(*[process_file(session, f) for f in batch])
                results.extend(batch_results)

        # Always save raw results to disk
        if not output_path:
            output_path = str(root / "_swarm_results.txt")
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w") as f:
            for name, answer in results:
                f.write(f"{name}: {answer}\n")

        # Synthesize — short summary for context, full results on disk
        all_text = " ".join(a.lower() for _, a in results)
        from collections import Counter
        words = all_text.split()
        bigrams = [f"{words[i]} {words[i+1]}" for i in range(len(words)-1)]
        common = Counter(bigrams).most_common(8)

        # Write a synthesis summary alongside the raw results
        summary_path = str(out).replace('.txt', '_summary.md')
        with open(summary_path, 'w') as f:
            f.write(f"# Analysis of {len(files)} files\n\n")
            f.write(f"**Question:** {question}\n\n")
            f.write("## Top Recurring Themes\n\n")
            for phrase, count in common:
                if count >= 3:
                    f.write(f"- **{phrase}** ({count}x)\n")
            f.write(f"\n## Sample Findings\n\n")
            for name, answer in results[:5]:
                f.write(f"- **{name}**: {answer[:200]}\n")
            f.write(f"\n*Full results in {output_path}*\n")

        summary = f"Analyzed {len(files)} files via {MAX_WORKERS} workers.\n"
        summary += "Top themes: " + ", ".join(f'{p} ({c}x)' for p, c in common if c >= 3) + "\n"
        summary += f"Results saved to {output_path}\n"
        summary += f"Summary saved to {summary_path}\n"
        summary += "Use message_result to deliver these findings."

        return ToolResult(summary)
