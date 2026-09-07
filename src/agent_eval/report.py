"""Render an Evaluation.

Two formats. Text is for a person reading a terminal, so it leads with what is
wrong and omits sections it has nothing to say about. JSON is for a machine, so it
is complete and includes derived values rather than making the consumer recompute
a failure rate the analysis already knows.
"""

from __future__ import annotations

import json
from dataclasses import asdict

from .metrics import Evaluation


def _duration(ms: int) -> str:
    if ms <= 0:
        return "unknown"
    seconds = ms / 1000
    if seconds < 90:
        return f"{seconds:.0f}s"
    minutes = seconds / 60
    if minutes < 90:
        return f"{minutes:.0f}m"
    return f"{minutes / 60:.1f}h"


def render_text(evaluation: Evaluation) -> str:
    """A short report for a human. Findings first, because that is the point."""
    lines: list[str] = []

    lines.append(f"Session {evaluation.session_id or '(unknown)'}")
    if evaluation.cwd:
        lines.append(f"  {evaluation.cwd}")
    lines.append("")

    lines.append("Findings")
    if evaluation.findings:
        lines.extend(f"  - {finding}" for finding in evaluation.findings)
    else:
        lines.append("  Nothing flagged.")
    lines.append("")

    tools = evaluation.tools
    lines.append("Tools")
    if tools.total_calls:
        lines.append(
            f"  {tools.total_calls} calls, {tools.total_failures} failed "
            f"({tools.overall_failure_rate:.0%})"
        )
        for name in sorted(tools.by_tool, key=lambda n: -tools.by_tool[n].calls):
            stat = tools.by_tool[name]
            suffix = f", {stat.failures} failed" if stat.failures else ""
            lines.append(f"    {name}: {stat.calls}{suffix}")
    else:
        lines.append("  No tool calls.")
    lines.append("")

    tests = evaluation.tests
    if tests.runs:
        state = {True: "green", False: "red", None: "unknown"}[tests.ended_green]
        lines.append("Tests")
        lines.append(f"  {tests.runs} runs, {tests.failures} failed, ended {state}")
        lines.append("")

    gates = evaluation.gates
    if gates.permission_modes or gates.hook_blocks:
        lines.append("Gates")
        if gates.permission_modes:
            lines.append(f"  permission mode: {', '.join(sorted(set(gates.permission_modes)))}")
        if gates.hook_blocks:
            lines.append(f"  hook blocks: {gates.hook_blocks}")
        lines.append("")

    cost = evaluation.cost
    if cost.total_usd or cost.lines_changed:
        lines.append("Cost")
        lines.append(f"  ${cost.total_usd:.2f} over {_duration(cost.duration_ms)}")
        if cost.usd_per_100_lines is not None:
            lines.append(
                f"  {cost.lines_changed} lines changed, "
                f"${cost.usd_per_100_lines:.2f} per 100 lines"
            )
        if cost.models:
            lines.append(f"  models: {', '.join(cost.models)}")
        lines.append("")

    lines.append(f"Human turns: {evaluation.human_turns}")
    return "\n".join(lines)


def to_dict(evaluation: Evaluation) -> dict:
    """The full evaluation, including values derived from properties."""
    payload = asdict(evaluation)
    payload["tools"]["overall_failure_rate"] = evaluation.tools.overall_failure_rate
    for name, stat in evaluation.tools.by_tool.items():
        payload["tools"]["by_tool"][name]["failure_rate"] = stat.failure_rate
    payload["cost"]["usd_per_100_lines"] = evaluation.cost.usd_per_100_lines
    return payload


def render_json(evaluation: Evaluation, indent: int = 2) -> str:
    """The full evaluation as JSON."""
    return json.dumps(to_dict(evaluation), indent=indent, sort_keys=True)
