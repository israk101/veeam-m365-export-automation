"""Self-contained HTML executive report generator for Veeam M365 Restore Tester."""

from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path
from typing import Any

from core.batch_report import normalize_status, overall_status
from core.paths import resource_path
from core.reports import human_size


def _get_logo_data_uri() -> str:
    """Read logos_logo.png and return base64 data URI, or empty string if not found."""
    for candidate in (
        resource_path("assets/logos_logo.png"),
        Path(r"C:\Users\Administrator\Downloads\drive-download-20260918T072946Z-1-001\Logo_Logos_trasparent (1).png"),
    ):
        if candidate.is_file():
            try:
                raw = candidate.read_bytes()
                b64 = base64.b64encode(raw).decode("ascii")
                return f"data:image/png;base64,{b64}"
            except Exception:
                continue
    return ""


def _get_footer_logo_data_uri() -> str:
    """Read logos_footer.png and return base64 data URI for the report footer."""
    for candidate in (
        resource_path("assets/logos_footer.png"),
        Path(r"C:\Users\Administrator\Desktop\logos footter.png"),
        resource_path("assets/logos_logo.png"),
    ):
        if candidate.is_file():
            try:
                raw = candidate.read_bytes()
                b64 = base64.b64encode(raw).decode("ascii")
                return f"data:image/png;base64,{b64}"
            except Exception:
                continue
    return ""


def _get_workload_logo_data_uri(workload_key: str) -> str:
    """Read workload-specific icon (email, onedrive, sharepoint) and return base64 URI."""
    mapping = {
        "email": ["assets/workload_email.png", r"C:\Users\Administrator\Desktop\email logo for html report.png"],
        "onedrive": ["assets/workload_onedrive.png", r"C:\Users\Administrator\Desktop\one drive cloude logo.png"],
        "sharepoint": ["assets/workload_sharepoint.png", r"C:\Users\Administrator\Desktop\share point logo.png"],
    }
    for item in mapping.get(workload_key.lower(), []):
        p = resource_path(item) if not item.startswith("C:") else Path(item)
        if p.is_file():
            try:
                raw = p.read_bytes()
                b64 = base64.b64encode(raw).decode("ascii")
                return f"data:image/png;base64,{b64}"
            except Exception:
                continue
    return ""


def _job_successful_workloads(j: dict[str, Any]) -> list[str]:
    """Return list of workloads (Exchange, OneDrive, SharePoint) that succeeded in this job's report."""
    rep = j.get("report")
    if not rep or not isinstance(rep, dict):
        return []
    passed = []
    for wl in ("Exchange", "OneDrive", "SharePoint"):
        val = rep.get(wl)
        if isinstance(val, dict) and str(val.get("Status", "")).upper() == "SUCCESS":
            passed.append(wl)
    return passed


def _job_has_workload_success(j: dict[str, Any]) -> bool:
    """Return True if the job finished with code 0, has AllSuccessful, or has at least one successful workload."""
    if j.get("exit_code") == 0:
        return True
    rep = j.get("report")
    if not rep or not isinstance(rep, dict):
        return False
    if rep.get("AllSuccessful"):
        return True
    return len(_job_successful_workloads(j)) > 0


def render_html_report(summary: dict[str, Any]) -> str:
    """Generate self-contained, print-ready HTML for the batch summary."""
    logo_uri = _get_logo_data_uri()
    footer_logo_uri = _get_footer_logo_data_uri()

    raw_ts = str(summary.get("RunTimestamp", ""))
    date_display = raw_ts
    if len(raw_ts) == 15 and "_" in raw_ts:
        try:
            dt = datetime.strptime(raw_ts, "%Y%m%d_%H%M%S")
            date_display = dt.strftime("%d/%m/%Y %H:%M:%S")
        except Exception:
            pass

    org = summary.get("Organization", "Unknown Organization")
    orgs_tested = summary.get("OrganizationsTested") or ([org] if org else [])
    orgs_display = ", ".join(orgs_tested) if orgs_tested else org

    aggregate_status = overall_status(summary)
    all_successful = aggregate_status == "SUCCESS"
    backup_mode = "Eseguito prima del ripristino" if summary.get("BackupExecuted") else "Ultimo restore point (backup saltato)"
    backup_status = str(summary.get("BackupStatus", "—"))

    # Workloads
    workloads = [
        ("email", "Email / Exchange", summary.get("Exchange") or {}),
        ("onedrive", "OneDrive", summary.get("OneDrive") or {}),
        ("sharepoint", "SharePoint Teams", summary.get("SharePoint") or {}),
    ]

    job_results = summary.get("JobResults") or []
    total_jobs = len(job_results)
    successful_jobs = sum(1 for j in job_results if _job_has_workload_success(j))

    # Build workload rows
    workload_rows_html = []
    for key, label, data in workloads:
        icon_uri = _get_workload_logo_data_uri(key)
        icon_tag = f'<img src="{icon_uri}" alt="{key}" style="width:20px;height:20px;vertical-align:middle;margin-right:8px;object-fit:contain;" />' if icon_uri else ''
        st = normalize_status(data.get("Status"), "N/A")
        badge_cls = "badge-success" if st == "SUCCESS" else ("badge-failed" if st in ("FAILED", "ERROR") else "badge-na")
        rp = str(data.get("RestorePointDate") or summary.get("RestorePointDate") or "—")
        user = str(data.get("SourceMailbox") or data.get("SourceUser") or data.get("Site") or "—")
        item = str(data.get("Subject") or data.get("FileName") or "—")
        user_item = f"<strong>{user}</strong>" + (f"<br><span class='sub-item'>{item}</span>" if item != "—" else "")
        sha = str(data.get("SHA256") or "—")
        size = human_size(data.get("SizeBytes")) if data.get("SizeBytes") else "—"

        workload_rows_html.append(f"""
        <tr>
            <td class="bold">{icon_tag}{label}</td>
            <td>{rp}</td>
            <td>{user_item}</td>
            <td>{size}</td>
            <td><code>{sha[:16]}…</code></td>
            <td class="text-center"><span class="badge {badge_cls}">{st}</span></td>
        </tr>
        """)

    # Build per-job rows
    job_rows_html = []
    for j in job_results:
        j_name = str(j.get("job", "—"))
        j_org = str(j.get("organization") or org)
        code = j.get("exit_code", "—")
        rep = j.get("report") or {}
        passed_wls = _job_successful_workloads(j)
        j_pass = _job_has_workload_success(j)

        if j_pass:
            if rep.get("AllSuccessful") or len(passed_wls) == 3:
                j_status = "SUCCESS"
                j_badge = "badge-success"
            elif passed_wls:
                j_status = f"SUCCESS ({', '.join(passed_wls)})"
                j_badge = "badge-success"
            else:
                j_status = "SUCCESS"
                j_badge = "badge-success"
        else:
            j_status = "FAILED"
            j_badge = "badge-failed"

        rp = str(rep.get("RestorePointDate") or "—")

        job_rows_html.append(f"""
        <tr>
            <td><strong>{j_org}</strong></td>
            <td>{j_name}</td>
            <td>{rp}</td>
            <td class="text-center"><code>{code}</code></td>
            <td class="text-center"><span class="badge {j_badge}">{j_status}</span></td>
        </tr>
        """)

    logo_img_tag = f'<img src="{logo_uri}" alt="Logos Technologies" class="header-logo" />' if logo_uri else '<span class="logo-text">LOGOS TECHNOLOGIES</span>'
    status_banner_cls = {
        "SUCCESS": "status-pass",
        "WARNING": "status-warning",
        "FAILED": "status-failed",
    }[aggregate_status]
    status_banner_title = f"RESTORE TEST {aggregate_status}"
    if all_successful:
        tested_wls = [
            label for key, label, data in workloads
            if str(data.get("Status", "")).upper() == "SUCCESS"
        ]
        wls_str = f" ({', '.join(tested_wls)})" if tested_wls else ""
        status_banner_desc = f"Tutti i job e carichi di lavoro selezionati{wls_str} sono stati estratti e verificati con successo con integrità hash SHA-256."
    else:
        status_banner_desc = (
            "Uno o più carichi di lavoro richiedono verifica."
            if aggregate_status == "WARNING"
            else "Il test di ripristino non è stato completato con successo."
        )

    return f"""<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Report Test Ripristino M365 - {orgs_display}</title>
    <style>
        :root {{
            --primary: #10B981;
            --primary-dark: #059669;
            --primary-light: #D1FAE5;
            --danger: #EF4444;
            --danger-light: #FEE2E2;
            --warning: #F59E0B;
            --warning-light: #FEF3C7;
            --success: #10B981;
            --success-light: #D1FAE5;
            --bg: #F9FAFB;
            --border: #E5E7EB;
            --text-dark: #111827;
            --text-muted: #6B7280;
            --code-bg: #F3F4F6;
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg);
            color: var(--text-dark);
            line-height: 1.5;
            padding: 24px;
        }}

        .container {{
            max-width: 1080px;
            margin: 0 auto;
            background: #FFFFFF;
            border-radius: 12px;
            border: 1px solid var(--border);
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
            overflow: hidden;
        }}

        /* Document Header Bar */
        .doc-header-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            background: #F3F4F6;
            border-bottom: 1px solid var(--border);
        }}

        .doc-header-table td {{
            padding: 8px 16px;
            border-right: 1px solid var(--border);
        }}
        .doc-header-table td:last-child {{
            border-right: none;
        }}
        .doc-header-table .label {{
            color: var(--text-muted);
            font-weight: 600;
        }}
        .doc-header-table .val {{
            color: var(--text-dark);
            font-weight: 700;
            margin-left: 4px;
        }}

        /* Main Header */
        .brand-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 24px 32px;
            border-bottom: 2px solid var(--primary);
            background: #FFFFFF;
        }}

        .header-logo {{
            max-height: 52px;
            max-width: 220px;
            object-fit: contain;
        }}

        .logo-text {{
            font-size: 20px;
            font-weight: 800;
            letter-spacing: 1px;
            color: #1E293B;
        }}

        .title-block {{
            text-align: right;
        }}

        .report-eyebrow {{
            font-size: 12px;
            font-weight: 700;
            color: var(--primary);
            text-transform: uppercase;
            letter-spacing: 1.5px;
        }}

        .report-title {{
            font-size: 22px;
            font-weight: 800;
            color: var(--text-dark);
        }}

        /* Status Banner */
        .status-banner {{
            padding: 20px 32px;
            border-bottom: 1px solid var(--border);
        }}
        .status-pass {{
            background: var(--success-light);
            border-left: 6px solid var(--success);
        }}
        .status-warning {{
            background: var(--warning-light);
            border-left: 6px solid var(--warning);
        }}
        .status-failed {{
            background: var(--danger-light);
            border-left: 6px solid var(--danger);
        }}
        .status-banner h2 {{
            font-size: 16px;
            font-weight: 800;
            margin-bottom: 4px;
            color: var(--text-dark);
        }}
        .status-banner p {{
            font-size: 13px;
            color: var(--text-muted);
        }}

        /* KPI Grid */
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 16px;
            padding: 24px 32px;
            background: #FAFAFA;
            border-bottom: 1px solid var(--border);
        }}

        .kpi-card {{
            background: #FFFFFF;
            padding: 16px;
            border-radius: 8px;
            border: 1px solid var(--border);
        }}
        .kpi-title {{
            font-size: 11px;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            margin-bottom: 6px;
        }}
        .kpi-value {{
            font-size: 20px;
            font-weight: 800;
            color: var(--text-dark);
        }}

        /* Content Area */
        .content-section {{
            padding: 28px 32px;
        }}

        .section-title {{
            font-size: 15px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--text-dark);
            margin-bottom: 14px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .section-title::before {{
            content: "";
            display: inline-block;
            width: 4px;
            height: 16px;
            background: var(--primary);
            border-radius: 2px;
        }}

        /* Data Tables */
        table.data-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            margin-bottom: 24px;
        }}

        table.data-table th {{
            background: #F8FAFC;
            color: var(--text-muted);
            font-weight: 600;
            text-transform: uppercase;
            font-size: 11px;
            letter-spacing: 0.5px;
            padding: 10px 14px;
            border-bottom: 1px solid var(--border);
            text-align: left;
        }}

        table.data-table td {{
            padding: 12px 14px;
            border-bottom: 1px solid #F1F5F9;
            color: var(--text-dark);
            vertical-align: top;
        }}

        table.data-table tr:hover td {{
            background: #F8FAFC;
        }}

        .sub-item {{
            font-size: 11px;
            color: var(--text-muted);
        }}

        code {{
            background: var(--code-bg);
            padding: 2px 6px;
            border-radius: 4px;
            font-family: Consolas, monospace;
            font-size: 11px;
        }}

        .bold {{ font-weight: 700; }}
        .text-center {{ text-align: center; }}

        /* Badges */
        .badge {{
            display: inline-block;
            padding: 4px 10px;
            border-radius: 999px;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.5px;
            text-align: center;
        }}
        .badge-success {{
            background: var(--success-light);
            color: var(--success);
        }}
        .badge-failed {{
            background: var(--danger-light);
            color: var(--danger);
        }}
        .badge-warning {{
            background: var(--warning-light);
            color: #92400E;
        }}
        .badge-na {{
            background: #E5E7EB;
            color: #4B5563;
        }}

        /* Client Info Box */
        .info-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 16px;
            margin-bottom: 24px;
            background: #F9FAFB;
            padding: 16px 20px;
            border-radius: 8px;
            border: 1px solid var(--border);
        }}
        .info-row {{
            font-size: 12px;
            display: flex;
            justify-content: space-between;
            padding: 4px 0;
            border-bottom: 1px dashed #E5E7EB;
        }}
        .info-row:last-child {{ border-bottom: none; }}
        .info-label {{ color: var(--text-muted); font-weight: 600; }}
        .info-val {{ color: var(--text-dark); font-weight: 700; }}

        /* Footer */
        .report-footer {{
            padding: 20px 32px;
            border-top: 1px solid var(--border);
            background: #F8FAFC;
            font-size: 11px;
            color: var(--text-muted);
            text-align: center;
            line-height: 1.6;
        }}

        .footer-logo-wrap {{
            margin-bottom: 10px;
        }}
        .footer-logo {{
            max-height: 28px;
            opacity: 0.85;
        }}

        @media print {{
            @page {{
                size: A4 portrait;
                margin: 6mm 8mm;
            }}
            html, body {{
                padding: 0 !important;
                margin: 0 !important;
                background: #FFF !important;
                -webkit-print-color-adjust: exact !important;
                print-color-adjust: exact !important;
            }}
            .container {{
                border: 1px solid #E5E7EB !important;
                border-radius: 8px !important;
                box-shadow: none !important;
                max-width: 100% !important;
            }}
            .doc-header-table td {{
                padding: 4px 10px !important;
                font-size: 10px !important;
            }}
            .brand-header {{
                padding: 12px 18px !important;
            }}
            .header-logo {{
                max-height: 40px !important;
            }}
            .report-title {{
                font-size: 18px !important;
            }}
            .status-banner {{
                padding: 10px 18px !important;
            }}
            .status-banner h2 {{
                font-size: 14px !important;
                margin-bottom: 2px !important;
            }}
            .status-banner p {{
                font-size: 12px !important;
            }}
            .kpi-grid {{
                padding: 10px 18px !important;
                gap: 10px !important;
            }}
            .kpi-card {{
                padding: 8px 12px !important;
            }}
            .kpi-value {{
                font-size: 18px !important;
            }}
            .content-section {{
                padding: 14px 18px !important;
            }}
            .section-title {{
                font-size: 13px !important;
                margin-bottom: 8px !important;
            }}
            .info-grid {{
                margin-bottom: 14px !important;
                padding: 10px 14px !important;
                gap: 8px !important;
            }}
            .info-row {{
                font-size: 11px !important;
                padding: 2px 0 !important;
            }}
            table.data-table {{
                margin-bottom: 14px !important;
                font-size: 11.5px !important;
            }}
            table.data-table th {{
                padding: 6px 10px !important;
                font-size: 10px !important;
            }}
            table.data-table td {{
                padding: 6px 10px !important;
            }}
            .report-footer {{
                padding: 10px 18px !important;
                font-size: 10px !important;
                page-break-inside: avoid !important;
                break-inside: avoid !important;
            }}
            .footer-logo {{
                max-height: 22px !important;
            }}
        }}
    </style>
</head>
<body>

<div class="container">
    <!-- Top Metadata Header (from Logos document standard) -->
    <table class="doc-header-table">
        <tr>
            <td><span class="label">TIPO DOCUMENTO:</span><span class="val">Test di ripristino</span></td>
            <td><span class="label">VERSIONE:</span><span class="val">1.0</span></td>
            <td><span class="label">STATO:</span><span class="val">Final</span></td>
            <td><span class="label">DATA:</span><span class="val">{date_display}</span></td>
            <td><span class="label">CLASSIFICAZIONE:</span><span class="val">Riservato</span></td>
        </tr>
    </table>

    <!-- Brand Header -->
    <div class="brand-header">
        <div>
            {logo_img_tag}
        </div>
        <div class="title-block">
            <div class="report-eyebrow">Veeam Backup for M365</div>
            <div class="report-title">Report Test di Ripristino</div>
        </div>
    </div>

    <!-- Status Banner -->
    <div class="status-banner {status_banner_cls}">
        <h2>{status_banner_title}</h2>
        <p>{status_banner_desc}</p>
    </div>

    <!-- KPI Summary Cards -->
    <div class="kpi-grid">
        <div class="kpi-card">
            <div class="kpi-title">Organizzazioni</div>
            <div class="kpi-value">{len(orgs_tested)}</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">Job Selezionati</div>
            <div class="kpi-value">{total_jobs}</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">Job Completati</div>
            <div class="kpi-value">{successful_jobs}/{total_jobs}</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">Esito Complessivo</div>
            <div class="kpi-value" style="color: {'var(--primary)' if all_successful else 'var(--danger)'};">
                {aggregate_status}
            </div>
        </div>
    </div>

    <div class="content-section">
        <!-- Configuration & Target Details -->
        <div class="section-title">Dati Generali del Test</div>
        <div class="info-grid">
            <div>
                <div class="info-row"><span class="info-label">Committente / Org:</span><span class="info-val">{orgs_display}</span></div>
                <div class="info-row"><span class="info-label">Modalità di Test:</span><span class="info-val">{backup_mode}</span></div>
                <div class="info-row"><span class="info-label">Stato Backup:</span><span class="info-val">{backup_status}</span></div>
            </div>
            <div>
                <div class="info-row"><span class="info-label">Data Esecuzione:</span><span class="info-val">{date_display}</span></div>
                <div class="info-row"><span class="info-label">Restore Point:</span><span class="info-val">{summary.get('RestorePointDate', '—')}</span></div>
                <div class="info-row"><span class="info-label">Tool di Test:</span><span class="info-val">Veeam M365 Restore Tester v1.2.4</span></div>
            </div>
        </div>

        <!-- Granular Workload Recovery Results -->
        <div class="section-title">Esito Ripristino Granulare per Carico di Lavoro</div>
        <table class="data-table">
            <thead>
                <tr>
                    <th>Tipologia</th>
                    <th>Restore Point</th>
                    <th>Elemento Estratto (User / File)</th>
                    <th>Dimensione</th>
                    <th>Hash SHA-256</th>
                    <th class="text-center">Esito</th>
                </tr>
            </thead>
            <tbody>
                {''.join(workload_rows_html)}
            </tbody>
        </table>

        <!-- Individual Job Execution Details -->
        <div class="section-title">Dettaglio Esecuzione Singoli Job</div>
        <table class="data-table">
            <thead>
                <tr>
                    <th>Organizzazione</th>
                    <th>Nome Job</th>
                    <th>Restore Point Rilevato</th>
                    <th class="text-center">Exit Code</th>
                    <th class="text-center">Esito</th>
                </tr>
            </thead>
            <tbody>
                {''.join(job_rows_html)}
            </tbody>
        </table>
    </div>

    <!-- Footer Notice from Logos Document Template -->
    <div class="report-footer">
        {f'<div class="footer-logo-wrap"><img src="{footer_logo_uri}" class="footer-logo" alt="Logos Technologies" style="max-width: 100%; height: auto; max-height: 48px; margin-bottom: 8px;" /></div>' if footer_logo_uri else ''}
        <p><strong>Logos Technologies S.r.l.</strong> · Sistemi di Backup & Disaster Recovery Gestiti</p>
        <p>Il presente documento redatto da Logos Technologies S.r.l. contiene cifre, statistiche ed indicazioni che sono basate sull'esperienza e/o su informazioni fornite dal Committente.<br>
        Il presente documento è da considerarsi confidenziale e non dovrà essere ceduto a terzi ad eccezione di Logos Technologies S.r.l. e del Committente.</p>
    </div>
</div>

</body>
</html>
"""


def write_html_report(root: Path | str, summary: dict[str, Any]) -> Path:
    """Write Report_Summary.html into the specified batch root directory."""
    dest = Path(root).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    out_path = dest / "Report_Summary.html"
    content = render_html_report(summary)
    out_path.write_text(content, encoding="utf-8")
    return out_path
