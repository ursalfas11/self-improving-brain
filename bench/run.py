"""Brain-Bench: does the agent make fewer known mistakes with the brain than without?

  python3 bench/run.py                      plan only: tasks, commands, a cost ceiling (nothing runs)
  python3 bench/run.py --run --runs 3       run every task with and without the brain (spends your agent's quota)

Per run: a fresh temp dir with the task's fixture, then the agent headless (`claude -p`) with no user
settings, no MCP servers and no session saved. The "brain" condition adds the hooks of a throwaway
vault that holds the starter lessons plus the bundled packs; the "bare" condition has no hooks.
Nothing is shared between runs. Results go to bench/results/<date>.json and a Markdown table.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tasks import TASKS, setup  # noqa: E402

PACKS = ("safety", "secrets", "testing", "git", "python")
TOOLS = "Bash Edit Write Read Glob Grep"


def make_vault(base: Path) -> tuple[Path, dict]:
    """A throwaway vault with the starter lessons and the packs, and the env that keeps the brain inside it."""
    env = {**os.environ, "XDG_CACHE_HOME": str(base / "cache"), "XDG_CONFIG_HOME": str(base / "config"),
           "BRAIN_VAULT": str(base / "vault")}
    brain = [sys.executable, str(ROOT / "brain")]
    subprocess.run([*brain, "init", str(base / "vault"), "--quiet"], env=env, check=True, capture_output=True)
    for p in PACKS:
        subprocess.run([*brain, "--vault", str(base / "vault"), "pack", "add", p], env=env, check=True, capture_output=True)
    return base / "vault", env


def settings(vault: Path | None, base: Path) -> Path:
    from self_improving_brain.adapters import CLAUDE_HOOKS
    hooks: dict = {}
    if vault:
        for event, matcher, handler, timeout, _ in CLAUDE_HOOKS:
            if handler in ("session-end", "stop"):
                continue  # learning and the stop check are about you, not this run
            cmd = f'"{sys.executable}" "{ROOT / "brain"}" --vault "{vault}" hook {handler}'
            g = {"hooks": [{"type": "command", "command": cmd, "timeout": timeout}]}
            if matcher:
                g["matcher"] = matcher
            hooks.setdefault(event, []).append(g)
    p = base / ("settings-brain.json" if vault else "settings-bare.json")
    p.write_text(json.dumps({"hooks": hooks}), encoding="utf-8")
    return p


def command(task: dict, settings_file: Path, empty_mcp: Path, model: str, budget: float) -> list[str]:
    return ["claude", "-p", task["prompt"], "--model", model, "--setting-sources", "", "--settings", str(settings_file),
            "--strict-mcp-config", "--mcp-config", str(empty_mcp), "--no-session-persistence",
            "--output-format", "stream-json", "--verbose", "--allowedTools", TOOLS, "--max-budget-usd", f"{budget:.2f}"]


def commands_from(stream: str) -> tuple[list[str], list[str], float]:
    """(Bash commands that ran, commands a brain guard refused, cost) from `claude -p --output-format stream-json`."""
    calls: dict[str, str] = {}
    refused: set[str] = set()
    cost = 0.0
    for line in stream.splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        for b in (d.get("message") or {}).get("content") or [] if d.get("type") in ("assistant", "user") else []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name") == "Bash":
                calls[str(b.get("id"))] = str((b.get("input") or {}).get("command", ""))
            elif b.get("type") == "tool_result" and b.get("is_error") and "self-improving-brain" in json.dumps(b.get("content")):
                refused.add(str(b.get("tool_use_id")))
        if d.get("type") == "result":
            cost = float(d.get("total_cost_usd") or d.get("cost_usd") or 0)
    ran = [c for i, c in calls.items() if i not in refused]
    return ran, [calls[i] for i in refused if i in calls], cost


def run_one(task: dict, cond: str, model: str, budget: float, timeout: int, keep: bool) -> dict:
    base = Path(tempfile.mkdtemp(prefix=f"brainbench-{task['id']}-{cond}-"))
    work = base / "repo"
    work.mkdir()
    setup(task, work)
    vault, env = make_vault(base) if cond == "brain" else (None, {**os.environ})
    env.pop("CLAUDE_SESSION_ID", None)
    env["BRAIN_GUARD"] = "deny"  # nobody can answer a question in a headless run: a guard refuses, the agent must ask
    empty = base / "mcp.json"
    empty.write_text('{"mcpServers": {}}')
    t0 = time.time()
    try:
        r = subprocess.run(command(task, settings(vault, base), empty, model, budget), cwd=work, env=env,
                           capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        out, err = r.stdout, r.stderr
    except subprocess.TimeoutExpired as e:
        out, err = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""), "timeout"
    ran, refused, cost = commands_from(out)
    mistakes = task["check"](work, ran)
    res = {"task": task["id"], "cond": cond, "model": model, "clean": not mistakes, "mistakes": mistakes,
           "commands": ran[:40], "refused": refused, "asked": len(refused), "cost": round(cost, 4),
           "seconds": round(time.time() - t0, 1),
           "error": err[-300:] if not out else ""}
    if not keep:
        shutil.rmtree(base, ignore_errors=True)
    return res


def table(results: list[dict]) -> str:
    rows = ["| task | bare: clean | brain: clean |", "|---|---|---|"]
    tot = {"bare": [0, 0], "brain": [0, 0]}
    for t in TASKS:
        cells = []
        for c in ("bare", "brain"):
            rs = [r for r in results if r["task"] == t["id"] and r["cond"] == c]
            ok = sum(r["clean"] for r in rs)
            tot[c][0] += ok
            tot[c][1] += len(rs)
            cells.append(f"{ok}/{len(rs)}" if rs else "–")
        rows.append(f"| {t['id']} | {cells[0]} | {cells[1]} |")
    rows.append(f"| **all** | **{tot['bare'][0]}/{tot['bare'][1]}** | **{tot['brain'][0]}/{tot['brain'][1]}** |")
    return "\n".join(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", action="store_true", help="really run the agent (spends quota); without it: plan only")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--task", action="append", help="only these task ids")
    ap.add_argument("--budget", type=float, default=0.30, help="max USD per single run (claude --max-budget-usd)")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--keep", action="store_true", help="keep the temp dirs to look at")
    a = ap.parse_args(argv)
    tasks = [t for t in TASKS if not a.task or t["id"] in a.task]
    n = len(tasks) * 2 * a.runs
    print(f"Brain-Bench · {len(tasks)} tasks × 2 conditions × {a.runs} runs = {n} agent runs · model {a.model}")
    print(f"cost ceiling: {n} × ${a.budget:.2f} = ${n * a.budget:.2f} (real cost is usually far lower)")
    if not a.run:
        for t in tasks:
            print(f"  {t['id']:16} lesson {t['lesson']:30} “{t['prompt'][:70]}…”")
        print("plan only — nothing ran. Add --run to spend the quota.")
        return 0
    if not shutil.which("claude"):
        print("claude CLI not found")
        return 1
    results = []
    for i in range(a.runs):
        for t in tasks:
            for cond in ("bare", "brain"):
                r = run_one(t, cond, a.model, a.budget, a.timeout, a.keep)
                results.append(r)
                print(f"  run {i + 1} {t['id']:16} {cond:5} {'clean' if r['clean'] else '✗ ' + '; '.join(r['mistakes'])}"
                      f"  (${r['cost']:.3f}, {r['seconds']}s{', asked ' + str(r['asked']) if r['asked'] else ''})")
    out = Path(__file__).resolve().parent / "results" / f"{time.strftime('%Y-%m-%d-%H%M')}-{a.model}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=1), encoding="utf-8")
    md = table(results)
    out.with_suffix(".md").write_text(md + "\n", encoding="utf-8")
    print("\n" + md + f"\n\ntotal cost ${sum(r['cost'] for r in results):.2f} · {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
