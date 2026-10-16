"""Write a self-contained HTML report for a phone-journey run."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote


_SENSITIVE_KEY = re.compile(r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|password|passwd|secret|credential)", re.I)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|password|passwd|secret|credential)\b\s*[:=]\s*)([^\s,;&]+)"
)


def _redact_text(value: str) -> str:
    value = _BEARER.sub("Bearer [redacted]", value)
    return _ASSIGNMENT.sub(r"\1[redacted]", value)


def _safe(value: Any) -> Any:
    """Copy displayable data while removing credential-shaped fields/values."""
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if _SENSITIVE_KEY.search(str(key)) else _safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_text(str(value))


def _text(value: Any, fallback: str = "—") -> str:
    if value is None or value == "":
        value = fallback
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(_safe(value), ensure_ascii=False, indent=2)
    return html.escape(_redact_text(str(value)), quote=True)


def _json(value: Any, *, limit: int = 24_000) -> str:
    try:
        result = json.dumps(_safe(value), ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        result = str(_safe(value))
    if len(result) > limit:
        result = result[:limit] + "\n… (truncated)"
    return html.escape(_redact_text(result), quote=True)


def _duration(value: Any) -> str:
    if value is None or value == "":
        return "—"
    try:
        ms = float(value)
        if ms < 1000:
            return f"{ms:g} ms"
        return f"{ms / 1000:.2f} s"
    except (TypeError, ValueError):
        return _text(value)


def _status(value: Any) -> str:
    status = str(value or "unknown").strip().lower()
    if status not in {"passed", "failed", "blocked"}:
        return status or "unknown"
    return status


def _screenshot_href(run_dir: Path, value: Any) -> str | None:
    """Return a report-local URL only for a screenshot contained by run_dir."""
    if not isinstance(value, (str, Path)) or not str(value):
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = run_dir / candidate
    try:
        resolved = candidate.resolve(strict=False)
        relative = resolved.relative_to(run_dir)
    except (OSError, ValueError):
        return None
    if relative == Path("."):
        return None
    return "./" + quote(relative.as_posix(), safe="/._-()")


def _stage_items(stages: Any) -> list[Any]:
    if isinstance(stages, dict):
        return [{"stage": name, "status": status} for name, status in stages.items()]
    if isinstance(stages, (list, tuple)):
        return list(stages)
    return []


def _stage_markup(stages: Any) -> str:
    items = _stage_items(stages)
    if not items:
        return '<p class="muted">No stage details recorded.</p>'
    rows = []
    for item in items:
        if isinstance(item, dict):
            label = item.get("stage", item.get("name", item.get("label", "Stage")))
            state = item.get("status", item.get("state", ""))
            extra = {k: v for k, v in item.items() if k not in {"stage", "name", "label", "status", "state"}}
            detail = f'<details><summary>Details</summary><pre>{_json(extra)}</pre></details>' if extra else ""
        else:
            label, state, detail = item, "", ""
        rows.append(
            f'<li><span>{_text(label)}</span><span class="stage-state">{_text(state, "")}</span>{detail}</li>'
        )
    return '<ul class="stage-list">' + "".join(rows) + "</ul>"


def _step_markup(run_dir: Path, steps: list[dict]) -> str:
    if not steps:
        return '<p class="muted empty">No steps were recorded.</p>'
    sections: list[str] = []
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            step = {"step": index, "observation": {"value": step}}
        observation = step.get("observation") or {}
        if not isinstance(observation, dict):
            observation = {"value": observation}
        decision = step.get("decision") or {}
        action = step.get("action") or {}
        if not isinstance(decision, dict):
            decision = {"value": decision}
        if not isinstance(action, dict):
            action = {"value": action}

        title = step.get("step", step.get("name", index))
        stage = step.get("stage", "")
        shot = _screenshot_href(run_dir, observation.get("screenshot"))
        if shot:
            screen = (
                f'<a class="shot" href="{html.escape(shot, quote=True)}" target="_blank" '
                f'rel="noopener"><img src="{html.escape(shot, quote=True)}" alt="Screenshot for step {_text(title)}" '
                f'loading="lazy"></a>'
            )
        else:
            screen = '<div class="shot missing">No screenshot</div>'

        nodes = observation.get("nodes")
        if isinstance(nodes, (list, dict)):
            count = len(nodes)
            node_content = f'<details><summary>Show node data ({count} items)</summary><pre>{_json(nodes)}</pre></details>'
        elif nodes is not None:
            node_content = f'<details><summary>Show node data</summary><pre>{_json(nodes)}</pre></details>'
        else:
            node_content = ""

        foreground = observation.get("foreground_activity")
        verification = step.get("verification")
        obs_ms = step.get("observation_ms", observation.get("latency_ms"))
        decision_ms = decision.get("latency_ms")
        sections.append(f'''
          <article class="step-card">
            <div class="step-heading">
              <div><span class="step-number">STEP {index:02d}</span><h3>{_text(title)}</h3></div>
              <span class="stage-pill">{_text(stage, "Unstaged")}</span>
            </div>
            <div class="step-body">
              {screen}
              <div class="step-info">
                <div class="facts">
                  <div><span>Action</span><strong>{_text(action.get("label", action.get("kind")))}</strong></div>
                  <div><span>Choice</span><strong>{_text(decision.get("choice"))}</strong></div>
                  <div><span>Confidence</span><strong>{_text(decision.get("confidence"))}</strong></div>
                  <div><span>Model</span><strong>{_text(decision.get("model"))}</strong></div>
                  <div><span>Observation</span><strong>{_duration(obs_ms)}</strong></div>
                  <div><span>Decision API</span><strong>{_duration(decision_ms)}</strong></div>
                </div>
                <p class="activity"><span>Foreground activity</span><code>{_text(foreground)}</code></p>
                {('<p class="activity"><span>Immediate decision goal</span>' + _text(step['decision_goal']) + '</p>') if step.get('decision_goal') else ''}
                {('<p class="verification"><span>Verification</span>' + _text(verification) + '</p>') if verification is not None else ''}
                {node_content}
                {('<details><summary>Full observation data</summary><pre>' + _json({k:v for k,v in observation.items() if k != "nodes" and k != "screenshot"}) + '</pre></details>') if any(k not in {"nodes", "screenshot", "foreground_activity"} for k in observation) else ''}
              </div>
            </div>
          </article>''')
    return "\n".join(sections)


def write_report(run_dir: Path, summary: dict, steps: list[dict]) -> Path:
    """Write ``index.html`` under *run_dir* and return its path."""
    run_dir = Path(run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    summary = summary if isinstance(summary, dict) else {}
    status = _status(summary.get("status"))
    driver = summary.get("driver", summary.get("runner", summary.get("mode", "jev")))
    driver_text = str(driver or "jev").strip().lower()
    if "adapter" in driver_text and "smoke" not in driver_text:
        driver_text = "adapter-smoke"
    if driver_text not in {"jev", "adapter-smoke"}:
        driver_text = _redact_text(driver_text)
    error = summary.get("error")
    error_html = f'<div class="error"><strong>Run error</strong><pre>{_json(error, limit=8_000)}</pre></div>' if error else ""

    page = f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>{_text(summary.get("journey"), "Phone journey")} · Run report</title>
  <style>
    :root {{ color-scheme: light dark; --bg:#f3f5f8; --panel:#fff; --ink:#182230; --muted:#64748b; --line:#dce3ec; --accent:#4263eb; --soft:#eef2ff; --shadow:0 12px 32px #1a294012; }}
    @media (prefers-color-scheme: dark) {{ :root {{ --bg:#11151c; --panel:#191f29; --ink:#e8edf4; --muted:#a2adbb; --line:#303a48; --accent:#91a7ff; --soft:#252d42; --shadow:0 12px 32px #0005; }} }}
    * {{ box-sizing:border-box }} body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif }}
    main {{ width:min(1100px,calc(100% - 36px)); margin:36px auto 70px }} header {{ display:flex; justify-content:space-between; gap:24px; align-items:flex-start; margin-bottom:24px }}
    h1 {{ font-size:clamp(1.7rem,4vw,2.6rem); letter-spacing:-.04em; line-height:1.15; margin:5px 0 8px; overflow-wrap:anywhere }} h2 {{ font-size:1.1rem; margin:0 0 14px }} h3 {{ font-size:1.15rem; margin:2px 0 0; overflow-wrap:anywhere }}
    .eyebrow,.step-number {{ color:var(--muted); font-size:.72rem; font-weight:750; letter-spacing:.11em; text-transform:uppercase }} .subhead {{ margin:0;color:var(--muted) }}
    .status {{ flex:none; padding:7px 13px; border-radius:999px; text-transform:uppercase; font-size:.78rem; font-weight:800; letter-spacing:.06em; background:#e2e8f0;color:#334155 }}
    .status.passed {{ background:#dcfce7;color:#166534 }} .status.failed {{ background:#fee2e2;color:#991b1b }} .status.blocked {{ background:#fef3c7;color:#92400e }}
    @media (prefers-color-scheme:dark) {{ .status.passed{{background:#123523;color:#8be0a9}} .status.failed{{background:#421d24;color:#ff9a9a}} .status.blocked{{background:#433414;color:#f5d479}} }}
    .panel,.step-card {{ background:var(--panel); border:1px solid var(--line); border-radius:16px; box-shadow:var(--shadow) }} .panel {{ padding:22px; margin-bottom:20px }}
    .summary-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(155px,1fr)); gap:15px 22px }} .summary-grid div,.facts div {{ min-width:0 }}
    .summary-grid span,.facts span,.activity span,.verification span {{ display:block; color:var(--muted); font-size:.76rem; font-weight:650; margin-bottom:3px }} .summary-grid strong {{ display:block; overflow-wrap:anywhere }}
    .stage-list {{ padding:0; margin:0; list-style:none; display:grid; gap:8px }} .stage-list li {{ padding:10px 12px; border-radius:9px; background:var(--bg); display:flex; align-items:center; flex-wrap:wrap; gap:10px }} .stage-state {{ color:var(--muted); margin-left:auto }}
    .stage-list details {{ width:100% }} .step-card {{ margin:0 0 15px; overflow:hidden }} .step-heading {{ padding:17px 20px; display:flex; justify-content:space-between; align-items:center; gap:15px; border-bottom:1px solid var(--line) }}
    .stage-pill {{ background:var(--soft); color:var(--accent); border-radius:999px; padding:4px 10px; font-size:.78rem; font-weight:700 }} .step-body {{ padding:18px; display:grid; grid-template-columns:minmax(220px, .85fr) minmax(0,1.4fr); gap:20px }}
    .shot {{ display:block; background:var(--bg); border:1px solid var(--line); border-radius:11px; overflow:hidden; align-self:start }} .shot img {{ display:block; width:100%; height:auto; max-height:480px; object-fit:contain }} .shot.missing {{ min-height:150px; display:grid;place-items:center;color:var(--muted) }}
    .facts {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px 18px }} .facts strong {{ display:block; overflow-wrap:anywhere }} .activity,.verification {{ margin:15px 0 0; overflow-wrap:anywhere }} code {{ font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:.86em;word-break:break-all }}
    details {{ margin-top:12px }} summary {{ cursor:pointer; color:var(--accent); font-size:.87rem; font-weight:650 }} pre {{ white-space:pre-wrap; overflow-wrap:anywhere; max-height:420px; overflow:auto; background:var(--bg); border:1px solid var(--line); border-radius:9px; padding:12px; font:12px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace }}
    .error {{ margin-top:18px; padding:14px; background:#fff1f2; border:1px solid #fecdd3; border-radius:10px; color:#881337 }} @media(prefers-color-scheme:dark){{.error{{background:#321d25;border-color:#63313d;color:#ffc4d0}} }}
    .muted {{ color:var(--muted) }} .empty {{ padding:20px; border:1px dashed var(--line); border-radius:12px }} footer {{ color:var(--muted);font-size:.8rem;text-align:center;margin-top:28px }}
    @media(max-width:680px) {{ main{{width:calc(100% - 22px);margin-top:22px}} header{{align-items:flex-start}} .step-body{{grid-template-columns:1fr}} .shot{{max-width:100%}} .step-heading{{padding:14px}} .panel{{padding:17px}} }}
  </style>
</head>
<body>
  <main>
    <header>
      <div><div class="eyebrow">Phone journey report · {_text(driver_text)}</div><h1>{_text(summary.get("journey"), "Phone journey")}</h1><p class="subhead">{_text(summary.get("device_label"), "Device not recorded")}</p></div>
      <span class="status {html.escape(status, quote=True)}">{_text(status)}</span>
    </header>
    <section class="panel"><h2>Run summary</h2><div class="summary-grid">
      <div><span>Journey</span><strong>{_text(summary.get("journey"))}</strong></div>
      <div><span>Device</span><strong>{_text(summary.get("device_label"))}</strong></div>
      <div><span>Driver</span><strong>{_text(driver_text)}</strong></div>
      <div><span>Started</span><strong>{_text(summary.get("started_at"))}</strong></div>
      <div><span>Finished</span><strong>{_text(summary.get("finished_at"))}</strong></div>
      <div><span>Steps</span><strong>{len(steps)}</strong></div>
    </div>{error_html}</section>
    <section class="panel"><h2>Stages</h2>{_stage_markup(summary.get("stages"))}</section>
    <section><h2>Step timeline</h2>{_step_markup(run_dir, steps)}</section>
    <footer>Generated locally · Screenshots link to files inside this run directory</footer>
  </main>
</body>
</html>
'''
    output = run_dir / "index.html"
    output.write_text(page, encoding="utf-8")
    return output
