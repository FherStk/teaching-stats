# -*- coding: utf-8 -*-
#!/usr/bin/env python
import csv
import json
import os
import sys
from datetime import date
from pathlib import Path

try:
    import psycopg2
except ImportError:
    print("ERROR: psycopg2 no está instalado. Ejecuta: pip install psycopg2-binary")
    sys.exit(1)

# ---------------------------------------------------------------------------
# .env loading (python-dotenv si disponible, parsing manual como fallback)
# ---------------------------------------------------------------------------
def _load_env():
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        print(f"ERROR: No se encontró el fichero .env en {env_path}")
        print("Copia .env.example a .env y rellena las credenciales.")
        sys.exit(1)

    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
    except ImportError:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())


def _get_connection():
    host     = os.environ.get("DB_HOST", "localhost")
    port     = os.environ.get("DB_PORT", "5432")
    dbname   = os.environ.get("DB_NAME")
    user     = os.environ.get("DB_USER")
    password = os.environ.get("DB_PASSWORD")

    missing = [k for k, v in {"DB_NAME": dbname, "DB_USER": user, "DB_PASSWORD": password}.items() if not v]
    if missing:
        print(f"ERROR: Faltan variables en .env: {', '.join(missing)}")
        sys.exit(1)

    conn = psycopg2.connect(
        host=host, port=port, dbname=dbname, user=user, password=password,
        options="-c search_path=django,reports"
    )
    return conn


def _load_subjects():
    csv_path = Path(__file__).parent / "data" / "subjects.csv"
    if not csv_path.exists():
        print(f"ERROR: No se encontró data/subjects.csv en {csv_path}")
        sys.exit(1)
    subjects = {}
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code, name = row["code"], row["name"]
            subjects[code] = name
            # The DB prefixes all codes with "MP" (e.g. "0179" → "MP0179", "C056" → "MPC056")
            subjects[f"MP{code}"] = name
    return subjects


def _load_trainer_aliases():
    csv_path = Path(__file__).parent / "data" / "trainer_aliases.csv"
    if not csv_path.exists():
        return {}
    with open(csv_path, encoding="utf-8") as f:
        return {row["alias"].strip(): row["canonical"].strip() for row in csv.DictReader(f) if row["alias"].strip()}


# ---------------------------------------------------------------------------
# Global state (same pattern as original report_from_postgresql.py)
# ---------------------------------------------------------------------------
OUTPUT_FOLDER = Path(__file__).parent / "reports"

table_columns = [
    "evaluation_id", "timestamp", "year", "level", "department",
    "degree", "group", "subject_code", "subject_name", "topic",
    "question_sort", "question_type", "question_statement", "value"
]
comment_caption = ""
global_data     = []
legend_text     = []
total_data      = []
table_rows      = []
n_respondents   = 0
n_comments      = 0
bar_colors      = []
dist_colors     = []


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------
def load_data(degree, subject_code, trainer, group, year, cursor):
    global global_data, legend_text, total_data, table_rows, comment_caption, n_respondents, n_comments

    # Average score per question
    cursor.execute(f"""
        SELECT question_statement, SUM(CAST(value AS FLOAT))/COUNT(question_statement), question_sort
        FROM reports.answer
        WHERE degree='{degree}' AND subject_code='{subject_code}' AND "group"='{group}'
              AND question_type='Numeric' AND value <> '' AND trainer='{trainer}' AND "year"={year}
        GROUP BY question_statement, question_sort
        ORDER BY question_sort
    """)
    legend_text = []
    global_data = []
    for row in cursor.fetchall():
        legend_text.append(row[0])
        global_data.append(row[1])

    # Text question label
    cursor.execute(f"""
        SELECT DISTINCT question_statement
        FROM reports.answer
        WHERE degree='{degree}' AND subject_code='{subject_code}' AND "group"='{group}'
              AND question_type='Text' AND trainer='{trainer}' AND "year"={year}
    """)
    data = cursor.fetchone()
    comment_caption = "" if data is None else data[0]

    # Distribution: count of each score value per question
    cursor.execute(f"""
        SELECT question_sort, COUNT("value"), "value"::integer
        FROM reports.answer
        WHERE degree='{degree}' AND subject_code='{subject_code}' AND "group"='{group}'
              AND question_type='Numeric' AND value <> '' AND trainer='{trainer}' AND "year"={year}
        GROUP BY question_sort, "value"
        ORDER BY question_sort, "value"
    """)
    total_data = []
    question_sort   = 1
    question_scores = [0] * 10
    for row in cursor.fetchall():
        if row[0] != question_sort:
            total_data.append(list(question_scores))
            question_sort   = row[0]
            question_scores = [0] * 10
        question_scores[row[2] - 1] = row[1]
    total_data.append(list(question_scores))

    # Full answer detail rows (for datatable / export)
    cursor.execute(f"""
        SELECT evaluation_id, timestamp, year, level, department, degree, "group",
               subject_code, subject_name, topic, question_sort, question_type,
               question_statement, TRIM("value")
        FROM reports.answer
        WHERE degree='{degree}' AND subject_code='{subject_code}' AND "group"='{group}'
              AND trainer='{trainer}' AND "year"={year}
        ORDER BY degree, subject_code, question_sort
    """)
    table_rows = []
    for row in cursor.fetchall():
        table_rows.append({
            table_columns[0]:  str(row[0])  if row[0]  is not None else "",
            table_columns[1]:  str(row[1])  if row[1]  is not None else "",
            table_columns[2]:  str(row[2])  if row[2]  is not None else "",
            table_columns[3]:  str(row[3])  if row[3]  is not None else "",
            table_columns[4]:  str(row[4])  if row[4]  is not None else "",
            table_columns[5]:  str(row[5])  if row[5]  is not None else "",
            table_columns[6]:  str(row[6])  if row[6]  is not None else "",
            table_columns[7]:  str(row[7])  if row[7]  is not None else "",
            table_columns[8]:  str(row[8])  if row[8]  is not None else "",
            table_columns[9]:  str(row[9])  if row[9]  is not None else "",
            table_columns[10]: str(row[10]) if row[10] is not None else "",
            table_columns[11]: str(row[11]) if row[11] is not None else "",
            table_columns[12]: str(row[12]) if row[12] is not None else "",
            table_columns[13]: str(row[13]).strip() if row[13] is not None else "",
        })

    # Unique respondents (numeric questions only)
    cursor.execute(f"""
        SELECT COUNT(DISTINCT evaluation_id)
        FROM reports.answer
        WHERE degree='{degree}' AND subject_code='{subject_code}' AND "group"='{group}'
              AND trainer='{trainer}' AND "year"={year} AND question_type='Numeric'
    """)
    n_respondents = (cursor.fetchone() or [0])[0] or 0

    # Non-empty text comments
    cursor.execute(f"""
        SELECT COUNT(*)
        FROM reports.answer
        WHERE degree='{degree}' AND subject_code='{subject_code}' AND "group"='{group}'
              AND trainer='{trainer}' AND "year"={year}
              AND question_type='Text' AND TRIM(value) <> ''
    """)
    n_comments = (cursor.fetchone() or [0])[0] or 0


def setup_data():
    global bar_colors, dist_colors

    def _traffic_light(score):
        if score < 5.0:   return "rgba(220, 53, 69, 0.85)"
        elif score < 7.0: return "rgba(255, 140, 0, 0.85)"
        elif score < 8.5: return "rgba(255, 193, 7, 0.85)"
        else:             return "rgba(40, 167, 69, 0.85)"

    bar_colors = [_traffic_light(v) for v in global_data]

    dist_colors = [
        "rgba(220, 53, 69, 0.8)",
        "rgba(233, 80, 48, 0.8)",
        "rgba(242, 110, 28, 0.8)",
        "rgba(247, 148, 15, 0.8)",
        "rgba(255, 193, 7, 0.8)",
        "rgba(210, 210, 25, 0.8)",
        "rgba(155, 210, 35, 0.8)",
        "rgba(90, 200, 55, 0.8)",
        "rgba(52, 185, 65, 0.8)",
        "rgba(40, 167, 69, 0.8)",
    ]


def generate_file(degree, subject_code, trainer, group, subjects, aliases):
    subject_name = subjects.get(subject_code)
    if subject_name is None:
        raise ValueError(
            f"No se encontró la asignatura '{subject_code}' en data/subjects.csv. "
            "Añade la fila correspondiente al fichero."
        )

    n_questions = len(legend_text)
    overall_avg = f"{sum(global_data)/n_questions:.1f}/10" if global_data else "—"
    avg_chart_height = max(320, n_questions * 100)
    short_labels = [f"P{i+1}" for i in range(n_questions)]
    export_cols = ', '.join(f'{{data: "{c}"}}' for c in table_columns)
    canonical_trainers = [aliases.get(t.strip(), t.strip()) for t in trainer.replace("/", ",").split(",") if t.strip()]
    trainer_display = ", ".join(canonical_trainers)

    template = f"""<!DOCTYPE html>
<html lang="ca">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Informe {subject_code}: {subject_name} ({group})</title>
    <link rel="stylesheet" href="https://cdn.datatables.net/v/dt/jq-3.3.1/jszip-2.5.0/dt-1.10.24/b-1.7.0/b-html5-1.7.0/datatables.min.css"/>
    <style>
        *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
        :root {{
            --brand: #970A2C; --brand-dark: #7a0822;
            --bg: #f5f7fa; --card: #ffffff; --border: #e2e8f0;
            --text: #1a202c; --muted: #718096;
            --shadow: 0 1px 3px rgba(0,0,0,.08), 0 4px 16px rgba(0,0,0,.04);
        }}
        body {{ font-family: system-ui, -apple-system, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); font-size: 15px; line-height: 1.5; }}
        header {{ background: #fff; color: var(--text); padding: 16px 28px; display: flex; align-items: center; gap: 20px; border-bottom: 4px solid var(--brand); box-shadow: var(--shadow); }}
        header img {{ height: 52px; width: auto; flex-shrink: 0; }}
        .header-text {{ border-left: 2px solid var(--brand); padding-left: 20px; }}
        header h1 {{ font-size: 1.5rem; font-weight: 800; color: var(--brand); letter-spacing: -.01em; }}
        header p {{ font-size: .95rem; color: var(--muted); margin-top: 5px; font-weight: 500; letter-spacing: .02em; }}
        main {{ max-width: 1100px; margin: 0 auto; padding: 20px 16px 40px; }}
        .kpi-row {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 20px; }}
        .kpi {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 16px 20px; box-shadow: var(--shadow); }}
        .kpi-value {{ font-size: 2rem; font-weight: 700; color: var(--brand); line-height: 1.1; }}
        .kpi-label {{ font-size: .75rem; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; margin-top: 4px; }}
        .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 22px 26px; box-shadow: var(--shadow); margin-bottom: 20px; }}
        .card-title {{ font-size: .78rem; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: .07em; margin-bottom: 18px; }}
        .chart-wrap {{ position: relative; }}
        .search-bar {{ display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }}
        .search-bar input {{ flex: 1; padding: 9px 14px; border: 1px solid var(--border); border-radius: 6px; font-size: .95rem; font-family: inherit; outline: none; transition: border-color .15s; }}
        .search-bar input:focus {{ border-color: var(--brand); box-shadow: 0 0 0 3px rgba(173,0,80,.1); }}
        .search-bar .count {{ font-size: .82rem; color: var(--muted); white-space: nowrap; }}
        mark {{ background: #fef08a; border-radius: 2px; padding: 0 2px; }}
        #comments td {{ font-size: .95rem; line-height: 1.65; padding: 10px 14px !important; }}
        .export-row {{ text-align: right; margin-bottom: 6px; }}
        .export-btn {{ display: inline-flex; align-items: center; gap: 6px; padding: 8px 16px; background: var(--brand); color: #fff; border: none; border-radius: 6px; font-size: .85rem; font-family: inherit; cursor: pointer; }}
        .export-btn:hover {{ background: var(--brand-dark); }}
        #export-wrap {{ display: none; }}
        @media (max-width: 600px) {{ .kpi-row {{ grid-template-columns: repeat(2, 1fr); }} }}
    </style>
</head>
<body>
<header>
    <img src="https://elpuig.xeill.net/logo.png" alt="Institut Puig Castellar">
    <div class="header-text">
        <h1>{degree} · {subject_code}: {subject_name}</h1>
        <p>{trainer_display} · Grup {group}</p>
    </div>
</header>
<main>
    <div class="kpi-row">
        <div class="kpi"><div class="kpi-value">{n_respondents}</div><div class="kpi-label">Alumnes</div></div>
        <div class="kpi"><div class="kpi-value">{n_questions}</div><div class="kpi-label">Preguntes</div></div>
        <div class="kpi"><div class="kpi-value">{overall_avg}</div><div class="kpi-label">Mitjana global</div></div>
        <div class="kpi"><div class="kpi-value">{n_comments}</div><div class="kpi-label">Comentaris</div></div>
    </div>

    <div class="card">
        <div class="card-title">Valoració mitjana per pregunta</div>
        <div class="chart-wrap" style="height:{avg_chart_height}px">
            <canvas id="avgChart"></canvas>
        </div>
    </div>

    <div class="card">
        <div class="card-title">Distribució de puntuacions per pregunta</div>
        <div class="chart-wrap" style="height:320px">
            <canvas id="distChart"></canvas>
        </div>
    </div>

    <div class="card">
        <div class="card-title">Comentaris</div>
        <div class="search-bar">
            <input type="search" id="commentSearch" placeholder="Cerca paraules clau…" autocomplete="off">
            <span class="count" id="commentCount"></span>
        </div>
        <table id="comments" style="width:100%">
            <thead><tr><th>{comment_caption or "Comentari"}</th></tr></thead>
        </table>
    </div>

    <div class="export-row">
        <button class="export-btn" id="exportTrigger">&#x2193; Exporta dades completes (Excel)</button>
    </div>
    <div id="export-wrap">
        <table id="export">
            <thead><tr>{(''.join('<th>' + c + '</th>' for c in table_columns))}</tr></thead>
        </table>
    </div>
</main>

<script src="https://cdn.jsdelivr.net/npm/chart.js@3.2.1/dist/chart.min.js"></script>
<script src="https://cdn.datatables.net/v/dt/jq-3.3.1/jszip-2.5.0/dt-1.10.24/af-2.3.6/b-1.7.0/b-html5-1.7.0/datatables.min.js"></script>
<script>
    const avgLabels   = {json.dumps(legend_text)};
    const shortLabels = {json.dumps(short_labels)};
    const avgData     = {json.dumps([round(v, 2) for v in global_data])};
    const avgColors   = {json.dumps(bar_colors)};
    const distData    = {json.dumps(total_data)};
    const distColors  = {json.dumps(dist_colors)};
    const fullData    = {json.dumps(table_rows)};

    function wrapLabel(text, maxLen) {{
        var words = text.split(' '), lines = [], current = '';
        for (var i = 0; i < words.length; i++) {{
            var candidate = current ? current + ' ' + words[i] : words[i];
            if (candidate.length > maxLen && current) {{ lines.push(current); current = words[i]; }}
            else {{ current = candidate; }}
        }}
        if (current) lines.push(current);
        return lines;
    }}
    const wrappedAvgLabels = avgLabels.map(function(l) {{ return wrapLabel(l, 38); }});

    // Chart 1: horizontal average bars
    new Chart(document.getElementById('avgChart'), {{
        type: 'bar',
        data: {{
            labels: wrappedAvgLabels,
            datasets: [{{
                label: 'Valoració mitjana',
                data: avgData,
                backgroundColor: avgColors,
                borderColor: avgColors,
                borderWidth: 1,
                borderRadius: 4,
            }}]
        }},
        options: {{
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            scales: {{
                x: {{ min: 0, max: 10, title: {{ display: true, text: 'Puntuació (0–10)' }} }},
                y: {{ ticks: {{ font: {{ size: 13 }}, autoSkip: false }} }}
            }},
            plugins: {{
                legend: {{ display: false }},
                tooltip: {{ callbacks: {{
                    title: function(items) {{ return avgLabels[items[0].dataIndex]; }},
                    label: function(ctx) {{ return ' Mitjana: ' + ctx.raw.toFixed(2) + '/10'; }}
                }} }}
            }}
        }},
        plugins: [{{
            id: 'valueLabels',
            afterDraw(chart) {{
                const ctx2 = chart.ctx;
                chart.data.datasets.forEach((ds, i) => {{
                    chart.getDatasetMeta(i).data.forEach((bar, idx) => {{
                        const val = ds.data[idx];
                        ctx2.save();
                        ctx2.fillStyle = '#1a202c';
                        ctx2.font = 'bold 12px system-ui, sans-serif';
                        ctx2.textAlign = 'left';
                        ctx2.textBaseline = 'middle';
                        ctx2.fillText(val.toFixed(1), bar.x + 6, bar.y);
                        ctx2.restore();
                    }});
                }});
            }}
        }}]
    }});

    // Chart 2: stacked distribution
    new Chart(document.getElementById('distChart'), {{
        type: 'bar',
        data: {{
            labels: shortLabels,
            datasets: distColors.map(function(color, s) {{
                return {{
                    label: (s + 1) + (s === 0 ? ' punt' : ' punts'),
                    data: distData.map(function(q) {{ return q[s]; }}),
                    backgroundColor: color,
                    borderColor: '#fff',
                    borderWidth: 1,
                }};
            }})
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            scales: {{
                x: {{ stacked: true, title: {{ display: true, text: 'Pregunta' }} }},
                y: {{ stacked: true, title: {{ display: true, text: 'Nombre de respostes' }} }}
            }},
            plugins: {{
                legend: {{ position: 'bottom', labels: {{ boxWidth: 14, font: {{ size: 11 }} }} }},
                tooltip: {{
                    callbacks: {{
                        title: function(items) {{ return avgLabels[items[0].dataIndex]; }},
                        label: function(ctx) {{
                            return ' ' + ctx.raw + ' alumne' + (ctx.raw !== 1 ? 's' : '') + ' → ' + ctx.dataset.label;
                        }}
                    }}
                }}
            }}
        }}
    }});

    // Comments table
    const textData = fullData.filter(function(r) {{ return r.question_type === 'Text' && r.value.trim() !== ''; }});
    const tbl = $('#comments').DataTable({{
        data: textData,
        columns: [{{data: 'value'}}],
        dom: 'rtip',
        pageLength: 25,
        language: {{
            info: 'Mostrant _START_–_END_ de _TOTAL_ comentaris',
            infoEmpty: 'Cap comentari',
            infoFiltered: ' (filtrats de _MAX_)',
            paginate: {{ previous: '‹', next: '›' }},
            zeroRecords: 'Cap resultat per a aquesta cerca',
        }}
    }});

    function updateCount() {{
        var info = tbl.page.info();
        document.getElementById('commentCount').textContent =
            info.recordsTotal === info.recordsDisplay
                ? info.recordsTotal + ' comentari' + (info.recordsTotal !== 1 ? 's' : '')
                : info.recordsDisplay + ' de ' + info.recordsTotal + ' comentaris';
    }}

    function applyHighlight(term) {{
        document.querySelectorAll('#comments td').forEach(function(td) {{
            var raw = td.dataset.raw !== undefined ? td.dataset.raw : td.textContent;
            td.dataset.raw = raw;
            if (!term.trim()) {{ td.innerHTML = raw; return; }}
            var lower = raw.toLowerCase();
            var lterm = term.toLowerCase();
            var result = '';
            var pos = 0;
            var idx;
            while ((idx = lower.indexOf(lterm, pos)) !== -1) {{
                result += raw.slice(pos, idx) + '<mark>' + raw.slice(idx, idx + lterm.length) + '</mark>';
                pos = idx + lterm.length;
            }}
            result += raw.slice(pos);
            td.innerHTML = result;
        }});
    }}

    tbl.on('draw.dt', function() {{
        updateCount();
        applyHighlight(document.getElementById('commentSearch').value.trim());
    }});
    tbl.draw();

    document.getElementById('commentSearch').addEventListener('input', function() {{
        tbl.search(this.value).draw();
    }});

    // Export
    var exportTbl = $('#export').DataTable({{
        data: fullData,
        columns: [{export_cols}],
        dom: 'Bfrtip',
        buttons: [{{ extend: 'excel', filename: 'informe_{degree}_{subject_code}_{group}' }}],
        paging: false,
        searching: false,
    }});
    document.getElementById('exportTrigger').addEventListener('click', function() {{ exportTbl.button(0).trigger(); }});
</script>
</body>
</html>"""

    filename = f"informe_{degree}_{subject_code}_{group}.html"
    individual_trainers = [t.strip() for t in trainer.replace("/", ",").split(",") if t.strip()]
    for t in individual_trainers:
        canonical = aliases.get(t, t)
        folder = OUTPUT_FOLDER / canonical
        folder.mkdir(parents=True, exist_ok=True)
        (folder / filename).write_text(template, encoding="utf-8")
        print(f"  Generat: {canonical}/{filename}")


def generate_reports(cursor, subjects, aliases):
    OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)

    year = date.today().year
    cursor.execute(f"""
        SELECT DISTINCT degree, subject_code, trainer, "group"
        FROM reports.answer
        WHERE "year"={year} AND topic='Assignatura'
        ORDER BY degree, subject_code
    """)
    rows = cursor.fetchall()

    if not rows:
        print(f"No hi ha dades per a l'any {year}.")
        return

    print(f"Generant {len(rows)} informe(s) per a l'any {year}...")
    for row in rows:
        degree, subject_code, trainer, group = row
        print(f"  Processant: {degree} / {subject_code} / {group} / {trainer}")
        load_data(degree, subject_code, trainer, group, year, cursor)
        setup_data()
        generate_file(degree, subject_code, trainer, group, subjects, aliases)

    print(f"\nFet. Informes disponibles a: {OUTPUT_FOLDER}")


def main():
    _load_env()
    subjects = _load_subjects()
    aliases = _load_trainer_aliases()
    conn = _get_connection()
    try:
        with conn.cursor() as cursor:
            generate_reports(cursor, subjects, aliases)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
