# -*- coding: utf-8 -*-
#!/usr/bin/env python
import csv
import os
import sys
from random import randrange
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
legend_colors   = []
legend_summary  = []
legend_list     = []
total_graph     = []


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------
def load_data(degree, subject_code, trainer, group, year, cursor):
    global global_data, legend_text, total_data, table_rows, comment_caption

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
            total_data.append(f"[{', '.join(str(x) for x in question_scores)}]")
            question_sort   = row[0]
            question_scores = [0] * 10
        question_scores[row[2] - 1] = row[1]
    total_data.append(question_scores)

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
    esc = "\\'"
    for row in cursor.fetchall():
        table_rows.append(f"""
            '{table_columns[0]}': '{row[0]}',
            '{table_columns[1]}': '{row[1]}',
            '{table_columns[2]}': '{row[2]}',
            '{table_columns[3]}': '{"" if row[3]  is None else row[3].replace("'",  esc)}',
            '{table_columns[4]}': '{"" if row[4]  is None else row[4].replace("'",  esc)}',
            '{table_columns[5]}': '{"" if row[5]  is None else row[5].replace("'",  esc)}',
            '{table_columns[6]}': '{"" if row[6]  is None else row[6].replace("'",  esc)}',
            '{table_columns[7]}': '{"" if row[7]  is None else row[7].replace("'",  esc)}',
            '{table_columns[8]}': '{"" if row[8]  is None else row[8].replace("'",  esc)}',
            '{table_columns[9]}': '{row[9]}',
            '{table_columns[10]}': '{row[10]}',
            '{table_columns[11]}': '{row[11]}',
            '{table_columns[12]}': '{"" if row[12] is None else row[12].replace("'", esc)}',
            '{table_columns[13]}': '{"" if row[13] is None else row[13].replace("'", esc)}',
        """.replace("'None'", "''").replace('\r', '').replace('\n', ''))


def setup_data():
    global legend_colors, legend_summary, legend_list, total_graph

    legend_colors = [
        "rgb(255, 99, 132, 0.25)",
        "rgb(75, 192, 192, 0.25)",
        "rgb(255, 205, 86, 0.25)",
        "rgb(54, 162, 235, 0.25)",
    ]
    for _ in range(len(legend_text) - len(legend_colors)):
        legend_colors.append(f"rgb({randrange(255)}, {randrange(255)}, {randrange(255)}, 0.25)")

    legend_summary = [f"Pregunta {i+1}" for i in range(len(legend_text))]
    legend_list    = [
        f"<div class='icon' style='background-color: {legend_colors[i]};'></div>{legend_text[i]}"
        for i in range(len(legend_text))
    ]
    total_graph = [
        f"""
            'label': 'Quantitat de valoracions en {legend_summary[i].lower()}',
            'data':  {total_data[i]},
            'backgroundColor': '{legend_colors[i]}',
            'borderColor': '{legend_colors[i]}'
        """
        for i in range(len(legend_text))
    ]


def generate_file(degree, subject_code, trainer, group, subjects, aliases):
    subject_name = subjects.get(subject_code)
    if subject_name is None:
        raise ValueError(
            f"No se encontró la asignatura '{subject_code}' en data/subjects.csv. "
            "Añade la fila correspondiente al fichero."
        )

    template = f"""<!DOCTYPE html>
<html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta http-equiv="X-UA-Compatible" content="IE=edge">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Dashboard for {subject_code}: {subject_name} ({group})</title>
        <link rel="stylesheet" type="text/css" href="https://cdn.datatables.net/v/dt/jq-3.3.1/jszip-2.5.0/dt-1.10.24/b-1.7.0/b-colvis-1.7.0/b-html5-1.7.0/b-print-1.7.0/cr-1.5.3/kt-2.6.1/r-2.2.7/sp-1.2.2/datatables.min.css"/>
        <style>
            body{{
                background-color: whitesmoke;
                padding-bottom: 10px;
            }}
            body,
            h1{{
                margin: 0px;
                font-family: 'Trebuchet MS', 'Lucida Sans Unicode', 'Lucida Grande', 'Lucida Sans', Arial, sans-serif;
            }}
            header{{
                background-color: #ad0050;
                color: white;
                padding: 10px;
            }}
            h1{{
                padding: 0px;
            }}
            h2{{
                font-weight: normal;
            }}
            .canvas,
            .table{{
                position: relative;
                width:100%;
            }}
            .canvas{{
                padding-bottom: 20px;
                height:400px !important;
            }}
            .box{{
                width: calc(50% - 36px);
                border: solid 1px grey;
                box-shadow: 1px 1px 10px #888888;
                border-radius: 5px;
                margin: 10px 10px 0px 10px;
                padding: 0px 10px 10px 10px;
                background-color: white;
            }}
            .left{{
                float: left;
                margin-right: 5px;
            }}
            .right{{
                float: right;
                margin-left: 5px;
            }}
            .full{{
                width: calc(100% - 42px);
                padding: 0px 10px 10px 10px;
            }}
            .clear{{
                clear: both;
            }}
            .content{{
                width: 100%;
                display: flex;
            }}
            .box h3{{
                margin-top: 10px;
            }}
            .box ol{{
                padding-left: 20px;
                margin-left: 40px;
            }}
            .box li{{
                cursor: pointer;
            }}
            .box li:hover{{
                color: darkgray;
            }}
            .box li.cross{{
                text-decoration: line-through;
            }}
            .box li .icon{{
                width: 28px;
                height: 10px;
                background-color: red;
                position: relative;
                display: inline-block;
                margin-left: -60px;
                margin-right: 35px;
            }}
            .hide table,
            .hide #export_filter,
            .hide #export_info,
            .hide #export_paginate{{
                display: none;
            }}
            .hide .dt-buttons{{
                margin-left: 10px;
            }}
            @media only screen and (max-width: 800px) {{
                .content{{
                    display: inline;
                }}
                .left,
                .right {{
                    float: none;
                    display: block;
                    margin:10px;
                    width: calc(100% - 42px);
                }}
            }}
        </style>
        <script src="https://cdn.jsdelivr.net/npm/chart.js@3.2.1/dist/chart.min.js"></script>
        <script type="text/javascript" src="https://cdnjs.cloudflare.com/ajax/libs/pdfmake/0.1.36/pdfmake.min.js"></script>
        <script type="text/javascript" src="https://cdnjs.cloudflare.com/ajax/libs/pdfmake/0.1.36/vfs_fonts.js"></script>
        <script type="text/javascript" src="https://cdn.datatables.net/v/dt/jq-3.3.1/jszip-2.5.0/dt-1.10.24/af-2.3.6/b-1.7.0/b-colvis-1.7.0/b-html5-1.7.0/b-print-1.7.0/cr-1.5.3/kt-2.6.1/r-2.2.7/sp-1.2.2/datatables.min.js"></script>
        <script type="text/javascript">
            window.onload = function () {{
                $.fn.dataTable.ext.search.push(
                    function (settings, searchData, index, rowData, counter) {{
                        if(settings.sTableId == "export") return true;
                        else return !(rowData["question_type"] === 'Numeric' || rowData["value"] === '');
                }});

                const globalData = {{
                    labels:  [{(', '.join('"' + item + '"' for item in legend_summary))}],
                    datasets: [
                        {{
                            label: 'Valoració mitjana',
                            data:  [{(', '.join("'" + str(round(item, 2)) + "'" for item in global_data))}],
                            backgroundColor: [{(', '.join('"' + item + '"' for item in legend_colors))}],
                            borderColor: [{(', '.join('"' + item + '"' for item in legend_colors))}]
                        }}
                    ]
                }};
                const totalData = {{
                    labels:  [{(', '.join("'" + str(item) + (" punt" if item == 1 else " punts") + "'" for item in range(1, 11)))}],
                    datasets:  [{(', '.join('{' + item + '}' for item in total_graph))}]
                }};
                var fullData = [{(', '.join('{' + item + '}' for item in table_rows))}];

                var globalChart = new Chart(document.getElementById('globalChart'), {{
                    type: 'bar',
                    data: globalData,
                    options: {{
                        responsive: true,
                        maintainAspectRatio: false,
                        scales: {{
                            x:{{
                                title: {{
                                    display: true,
                                    text: 'Pregunta'
                                }}
                            }},
                            y:{{
                                suggestedMin: 0,
                                suggestedMax: 10,
                                title: {{
                                    display: true,
                                    text: 'Mitjana de valoracions'
                                }}
                            }}
                        }},
                        plugins: {{
                            legend: {{
                                display: false
                            }}
                        }}
                    }},
                }});
                var totalChart = new Chart(document.getElementById('totalChart'), {{
                    type: 'bar',
                    data: totalData,
                    options: {{
                        responsive: true,
                        maintainAspectRatio: false,
                        scales: {{
                            x:{{
                                title: {{
                                    display: true,
                                    text: 'Puntuació'
                                }}
                            }},
                            y:{{
                                title: {{
                                    display: true,
                                    text: 'Quantitat de valoracions'
                                }}
                            }}
                        }},
                        plugins: {{
                            legend: {{
                                display: false
                            }}
                        }}
                    }},
                }});
                $('#comments').DataTable({{
                    data: fullData,
                    columns: [{{data: "value"}}]
                }});
                $('#export').DataTable({{
                    data: fullData,
                    columns: [{(', '.join('{data: "' + item + '"}' for item in table_columns))}],
                    dom: 'Bfrtip',
                    buttons: ['copy', 'excel']
                }});

                var legendItems = document.querySelector('#legend').getElementsByTagName('li');
                for (var i = 0; i < legendItems.length; i++) {{
                    legendItems[i].addEventListener("click", legendClickCallback.bind(this,i), false);
                }}
                function legendClickCallback(legendItemIndex){{
                    var legendItem = document.querySelector('#legend').getElementsByTagName('li')[legendItemIndex];
                    legendItem.classList.toggle("cross");
                    document.querySelectorAll('canvas').forEach((chartItem,index)=>{{
                        var chart = Chart.instances[index];
                        if(chart.canvas.id == "globalChart") chart.toggleDataVisibility(legendItemIndex);
                        else chart.data.datasets[legendItemIndex].hidden = !(chart.data.datasets[legendItemIndex].hidden ?? false);
                        chart.update();
                    }});
                }}
            }};
        </script>
    <head>

    <body>
        <header>
            <h1>{degree}</h1>
            <h2>{subject_code}: {subject_name} ({group})</h2>
        </header>
        <div class="box full">
            <h3>Preguntes</h3>
            <ol id="legend">
                {(''.join('<li>' + item + '</li>' for item in legend_list))}
            </ol>
        </div>
        <div class="content">
            <div class="box left">
                <h3>Valoracions totals (quantitat de puntuacions per pregunta)</h3>
                <div class="canvas">
                    <canvas id="totalChart"></canvas>
                </div>
            </div>
            <div class="box right">
                <h3>Valoracions globals (mitjana de valoracions per pregunta)</h3>
                <div class="canvas">
                    <canvas id="globalChart"></canvas>
                </div>
            </div>
        </div>
        <div class="clear"></div>

        <div class="box full">
            <h3>Comentaris</h3>
            <div class="table">
                <table id="comments" style="width:100%">
                    <thead>
                        <tr>
                            <th>{comment_caption}</th>
                        </tr>
                    </thead>
                </table>
            </div>
        </div>

        <div class="box hide full">
            <h3>Exportació de dades</h3>
            <table id="export">
                <thead>
                    <tr>
                        {(''.join('<th>' + item + '</th>' for item in table_columns))}
                    </tr>
                </thead>
            </table>
        </div>

    </body>

</html>
"""

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
