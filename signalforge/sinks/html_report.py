"""Single-file HTML report of a pipeline run. No server required."""
from __future__ import annotations

from pathlib import Path

from jinja2 import Template

from signalforge.models import Draft, EnrichedAccount, EvalScore, PipelineRun, ResearchBrief

TEMPLATE = Template("""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>SignalForge run {{ run.run_id }}</title>
<style>
  body { font: 14px/1.5 -apple-system, system-ui, sans-serif; max-width: 1100px; margin: 2rem auto; padding: 0 1rem; color: #111; }
  h1, h2, h3 { margin: 1.2em 0 .4em; }
  h1 { font-size: 1.5rem; }
  .meta { color: #555; font-size: .9rem; }
  .summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: .75rem; margin-top: 1rem; }
  .kpi { background: #f5f5f5; padding: .75rem; border-radius: 6px; }
  .kpi .n { font-size: 1.4rem; font-weight: 600; }
  .account { border: 1px solid #e5e5e5; border-radius: 8px; padding: 1rem; margin: .8rem 0; }
  .score-pill { display: inline-block; padding: .1rem .5rem; border-radius: 12px; background: #eef; font-weight: 600; }
  .score-hi { background: #d4f8d4; }
  .score-md { background: #fdf6c5; }
  .score-lo { background: #fadadd; }
  .draft { background: #fafafa; padding: .6rem .8rem; border-left: 3px solid #888; margin: .5rem 0; white-space: pre-wrap; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .85rem; }
  .dim { display: inline-block; font-size: .75rem; background: #eee; padding: .1rem .4rem; border-radius: 10px; margin-right: .3rem; }
  .flag { color: #a00; font-size: .8rem; }
  .signals { font-size: .82rem; color: #444; }
  .signals li { margin: .1rem 0; }
  details summary { cursor: pointer; color: #555; }
  .subscores { display: flex; gap: .4rem; margin: .3rem 0 .1rem; font-size: .78rem; color: #333; }
  .subscore { background: #eef3ff; padding: .1rem .45rem; border-radius: 10px; }
  .subscore .lbl { color: #667; margin-right: .25rem; }
  .contacts { font-size: .82rem; color: #444; margin: .3rem 0 0; }
  .contacts li { margin: .1rem 0; }
  .falsify { margin-top: .5rem; padding: .4rem .6rem; background: #fff8e1; border-left: 3px solid #caa60a; font-size: .82rem; }
  .falsify .label { font-weight: 600; color: #7a6500; margin-right: .3rem; }
  .falsify ul { margin: .2rem 0 0 1rem; padding: 0; }
  .falsify li { margin: .1rem 0; }
</style>
</head>
<body>
<h1>SignalForge run <code>{{ run.run_id }}</code></h1>
<div class="meta">started {{ run.started_at }}{% if run.finished_at %} · finished {{ run.finished_at }}{% endif %} · config hash <code>{{ run.config_hash }}</code></div>

<div class="summary">
  <div class="kpi"><div class="n">{{ run.accounts_processed }}</div><div>accounts</div></div>
  <div class="kpi"><div class="n">{{ run.signals_ingested }}</div><div>signals</div></div>
  <div class="kpi"><div class="n">{{ run.drafts_generated }}</div><div>drafts</div></div>
  <div class="kpi"><div class="n">{{ "%.1f"|format(run.avg_draft_score or 0) }}</div><div>avg draft score</div></div>
</div>

<h2>Accounts ({{ rows|length }})</h2>

{% for account, brief, draft, score in rows %}
  <div class="account">
    <h3>
      {{ account.company.name or account.company.domain }}
      <span class="score-pill {% if account.icp_score >= 70 %}score-hi{% elif account.icp_score >= 40 %}score-md{% else %}score-lo{% endif %}">ICP {{ "%.0f"|format(account.icp_score) }}</span>
      <span class="score-pill {% if score.overall >= 80 %}score-hi{% elif score.overall >= 65 %}score-md{% else %}score-lo{% endif %}">draft {{ "%.0f"|format(score.overall) }}</span>
    </h3>
    <div class="meta"><code>{{ account.company.domain }}</code> · {{ account.signals|length }} signals{% if account.contacts %} · {{ account.contacts|length }} contacts{% endif %}</div>

    <div class="subscores">
      <span class="subscore"><span class="lbl">authenticity</span>{{ "%.0f"|format(account.authenticity) }}</span>
      <span class="subscore"><span class="lbl">authority</span>{{ "%.0f"|format(account.authority) }}</span>
      <span class="subscore"><span class="lbl">warmth</span>{{ "%.0f"|format(account.warmth) }}</span>
    </div>

    {% if account.contacts %}
    <details>
      <summary><strong>{{ account.contacts|length }} contact(s)</strong></summary>
      <ul class="contacts">
        {% for c in account.contacts %}
          <li>{{ c.full_name }} — {{ c.title }}{% if c.email %} · <a href="mailto:{{ c.email }}">{{ c.email }}</a>{% endif %}{% if c.linkedin_url %} · <a href="{{ c.linkedin_url }}">LinkedIn</a>{% endif %} <span class="meta">({{ c.source }})</span></li>
        {% endfor %}
      </ul>
    </details>
    {% endif %}

    <details open>
      <summary><strong>Why now</strong> — {{ brief.headline }}</summary>
      <p>{{ brief.why_now }}</p>
      {% if brief.hooks %}
      <ul class="signals">
        {% for h in brief.hooks %}<li>{{ h }}</li>{% endfor %}
      </ul>
      {% endif %}
    </details>

    <details>
      <summary><strong>{{ account.signals|length }} signals captured</strong></summary>
      <ul class="signals">
        {% for s in account.signals %}
          <li>[{{ s.kind.value }} · {{ s.source }} · strength {{ "%.2f"|format(s.strength) }}] <a href="{{ s.url }}">{{ s.title }}</a></li>
        {% endfor %}
      </ul>
    </details>

    <details open>
      <summary><strong>Best draft</strong>{% if draft.subject %} — {{ draft.subject }}{% endif %}</summary>
      <div class="draft">{{ draft.body }}</div>
      <div>
        {% for k, v in score.dimensions.items() %}
          <span class="dim">{{ k }}: {{ "%.0f"|format(v) }}</span>
        {% endfor %}
      </div>
      {% if score.flagged %}<div class="flag">⚠ flagged: {{ score.flagged|join(", ") }}</div>{% endif %}
      {% if score.rationale %}<div class="meta">judge: {{ score.rationale }}</div>{% endif %}
      {% if score.falsification_notes %}
      <div class="falsify">
        <span class="label">Falsifications</span>
        <span class="meta">(conditions under which this score would be wrong)</span>
        <ul>
          {% for note in score.falsification_notes %}<li>{{ note }}</li>{% endfor %}
        </ul>
      </div>
      {% endif %}
    </details>
  </div>
{% endfor %}

</body>
</html>
""")


def write_html_report(
    path: Path,
    run: PipelineRun,
    rows: list[tuple[EnrichedAccount, ResearchBrief, Draft, EvalScore]],
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    html = TEMPLATE.render(run=run, rows=rows)
    path.write_text(html)
    return path
