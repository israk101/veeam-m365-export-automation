from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from time import monotonic


WORKLOADS = ("Exchange", "OneDrive", "SharePoint")

WORKLOAD_RESTORE_FOLDERS = {
    "Exchange": "restore email",
    "OneDrive": "restore one drive",
    "SharePoint": "restore share point",
}


def normalize_status(value: Any, default: str = "N/A") -> str:
    """Return a stable English status label for UI and client reports."""
    raw = str(value or "").strip().upper().replace("_", " ")
    aliases = {
        "SUCCESSO": "SUCCESS",
        "PASSED": "SUCCESS",
        "PASS": "SUCCESS",
        "FALLITO": "FAILED",
        "ERROR": "FAILED",
        "ATTENZIONE": "WARNING",
        "NEEDS ATTENTION": "WARNING",
        "WARN": "WARNING",
        "NON CONFIGURATO": "NOT CONFIGURED",
        "NON CONFIGURATO NEL JOB": "NOT CONFIGURED",
    }
    return aliases.get(raw, raw or default)


def overall_status(summary: dict[str, Any]) -> str:
    """Classify the aggregate result as SUCCESS, WARNING, or FAILED."""
    explicit = normalize_status(summary.get("OverallStatus"), "")
    if explicit in {"SUCCESS", "WARNING", "FAILED"}:
        return explicit
    if summary.get("AllSuccessful"):
        return "SUCCESS"

    statuses = [
        normalize_status((summary.get(workload) or {}).get("Status"), "")
        for workload in WORKLOADS
        if isinstance(summary.get(workload), dict)
    ]
    success_count = sum(status == "SUCCESS" for status in statuses)
    failure_count = sum(status == "FAILED" for status in statuses)
    if failure_count:
        return "WARNING" if success_count else "FAILED"
    if success_count:
        return "WARNING"

    exit_codes = [entry.get("exit_code") for entry in summary.get("JobResults", [])]
    if any(code not in (None, 0) for code in exit_codes):
        return "FAILED"
    return "WARNING"


def client_report_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Remove internal-only telemetry before rendering client-facing reports."""
    client_summary = deepcopy(summary)
    client_summary.pop("DurationSeconds", None)
    client_summary.pop("ReportDurationSeconds", None)
    for entry in client_summary.get("JobResults", []):
        entry.pop("duration_seconds", None)
        report = entry.get("report")
        if isinstance(report, dict):
            report.pop("DurationSeconds", None)
            report.pop("PhaseTimings", None)
    return client_summary


def copy_workload_artifacts(directory: Path, summary: dict[str, Any]) -> None:
    """Copy restored files from job run directories into the batch evidence folder."""
    import shutil

    for workload, folder_name in WORKLOAD_RESTORE_FOLDERS.items():
        candidates: list[dict[str, Any]] = []
        item = summary.get(workload)
        if isinstance(item, dict) and item.get("LocalFile"):
            candidates.append(item)
        for entry in summary.get("JobResults", []):
            rep = entry.get("report")
            if isinstance(rep, dict):
                wl_data = rep.get(workload)
                if isinstance(wl_data, dict) and wl_data.get("LocalFile"):
                    candidates.append(wl_data)

        copied: dict[Path, Path] = {}
        for item in candidates:
            src = Path(item["LocalFile"]).resolve()
            if src in copied:
                item["LocalFile"] = str(copied[src])
                continue
            if not src.is_file():
                raise FileNotFoundError(f"Missing {workload} evidence: {src}")
            dest_dir = directory / folder_name
            dest_dir.mkdir(parents=True, exist_ok=True)
            # Preserve each job's evidence; identical filenames need distinct paths.
            for sibling in (src, *(p for p in src.parent.iterdir() if p.is_file() and p != src)):
                if sibling in copied:
                    continue
                target = dest_dir / sibling.name
                suffix = 1
                while target.exists() and target.resolve() != sibling:
                    target = dest_dir / f"{sibling.stem}_{suffix}{sibling.suffix}"
                    suffix += 1
                if target.resolve() != sibling:
                    shutil.copy2(sibling, target)
                copied[sibling] = target
                copied[target.resolve()] = target
            item["LocalFile"] = str(copied[src])


def build_batch_summary(
    organization: str,
    jobs: list[dict[str, Any]],
    skip_backup: bool,
    timestamp: datetime | None = None,
    duration_seconds: int | float | None = None,
) -> dict[str, Any]:
    now = timestamp or datetime.now()
    jobs = deepcopy(jobs)
    org_groups: dict[str, list[dict[str, Any]]] = {}
    for entry in jobs:
        org = entry.get("organization", organization)
        org_groups.setdefault(org, []).append(entry)

    orgs_tested = list(org_groups.keys()) if org_groups else ([organization] if organization else [])

    summary: dict[str, Any] = {
        "RunTimestamp": now.strftime("%Y%m%d_%H%M%S"),
        "Organization": (
            organization if len(orgs_tested) <= 1
            else f"{len(orgs_tested)} organizations"
        ),
        "OrganizationsTested": orgs_tested,
        "OrganizationResults": org_groups,
        "JobName": f"{len(jobs)} selected jobs",
        "SelectedJobs": [entry["job"] for entry in jobs],
        "BackupExecuted": not skip_backup,
        "BackupStatus": "SkippedByUser" if skip_backup else "Completed per selected job",
        "RestorePointDate": "Multiple restore points",
        "JobResults": jobs,
        "AllSuccessful": False,
    }
    if duration_seconds is not None:
        summary["DurationSeconds"] = max(0, int(round(float(duration_seconds))))
    for workload in WORKLOADS:
        candidates = [
            entry.get("report", {}).get(workload)
            for entry in jobs
            if isinstance(entry.get("report"), dict)
        ]
        successful = next(
            (item for item in candidates if isinstance(item, dict) and str(item.get("Status", "")).upper() == "SUCCESS"),
            None,
        )
        summary[workload] = successful or next((item for item in candidates if isinstance(item, dict)), {"Status": "N/A"})

    def _job_is_successful(entry: dict[str, Any]) -> bool:
        code = entry.get("exit_code")
        rep = entry.get("report")
        if isinstance(rep, dict):
            if code != 0 or rep.get("FatalError") or rep.get("CleanupErrors") or entry.get("cancelled"):
                return False
            statuses = [
                str(rep.get(wl, {}).get("Status", "")).upper()
                for wl in WORKLOADS
                if isinstance(rep.get(wl), dict)
            ]
            has_success = any(s == "SUCCESS" for s in statuses)
            has_failure = any(s in ("FAILED", "ERROR") for s in statuses)
            if has_success and not has_failure:
                return True
        return False

    any_workload_success = any(
        str(summary.get(workload, {}).get("Status", "")).upper() == "SUCCESS"
        for workload in WORKLOADS
    )
    any_workload_failed = any(
        str(summary.get(workload, {}).get("Status", "")).upper() in ("FAILED", "ERROR")
        for workload in WORKLOADS
    )
    all_jobs_passed = bool(jobs) and all(_job_is_successful(entry) for entry in jobs)

    summary["AllSuccessful"] = all_jobs_passed and any_workload_success and not any_workload_failed
    summary["OverallStatus"] = overall_status(summary)
    return summary


def build_organization_summaries(
    jobs: list[dict[str, Any]],
    skip_backup: bool,
    timestamp: datetime | None = None,
) -> list[dict[str, Any]]:
    """Build one independent summary for each organization in job order."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in jobs:
        organization = str(entry.get("organization", "")).strip() or "Unknown organization"
        grouped.setdefault(organization, []).append(entry)

    summaries: list[dict[str, Any]] = []
    for organization, organization_jobs in grouped.items():
        duration = sum(
            max(0, float(entry.get("duration_seconds", 0) or 0))
            for entry in organization_jobs
        )
        summaries.append(build_batch_summary(
            organization,
            organization_jobs,
            skip_backup,
            timestamp,
            duration_seconds=duration,
        ))
    return summaries


def write_batch_report(
    root: str | Path,
    summary: dict[str, Any],
    max_keep: int = 5,
    report_formats: list[str] | tuple[str, ...] | set[str] | None = None,
) -> Path:
    report_started = monotonic()
    selected_formats = {str(item).lower() for item in (report_formats if report_formats is not None else ("txt", "html", "pdf"))}
    base = Path(root).expanduser()

    # Determine the primary organization name for directory placement
    orgs_tested = summary.get("OrganizationsTested") or []
    org_name = summary.get("Organization", "")
    timestamp = summary.get("RunTimestamp", datetime.now().strftime("%Y%m%d_%H%M%S"))

    if len(orgs_tested) == 1:
        primary_org = orgs_tested[0]
    elif org_name and "organization" not in org_name.lower():
        primary_org = org_name
    else:
        primary_org = ""

    if primary_org:
        from core.retention import sanitize_org_name, enforce_retention
        safe_org = sanitize_org_name(primary_org)
        org_dir = base / safe_org
        directory = org_dir / f"RestoreTest_{timestamp}"
    else:
        # Legacy fallback for multi-org or unknown
        directory = base / f"Batch_{timestamp}"
        org_dir = None

    # Reserve a new session directory, preserving the existing timestamp layout.
    stamp = datetime.strptime(timestamp, "%Y%m%d_%H%M%S")
    while True:
        try:
            directory.mkdir(parents=True, exist_ok=False)
            break
        except FileExistsError:
            stamp += timedelta(seconds=1)
            directory = directory.with_name(f"{'RestoreTest' if org_dir else 'Batch'}_{stamp:%Y%m%d_%H%M%S}")
    try:
        copy_workload_artifacts(directory, summary)
    except Exception:
        # This invocation created the directory; original evidence is still in
        # staging. Do not leave a partial session consuming retention slots.
        import shutil
        if directory.resolve().parent == (org_dir or base).resolve():
            shutil.rmtree(directory)
        raise
    json_path = directory / "Report_Summary.json"
    # Preserve mandatory evidence even if an optional renderer fails unexpectedly.
    temporary = json_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(json_path)
    status = overall_status(summary)
    lines = [
        "VEEAM M365 MULTI-JOB RESTORE TEST",
        "=" * 48,
        f"Organization : {summary.get('Organization')}",
        f"Jobs         : {', '.join(summary.get('SelectedJobs', []))}",
        f"Backup mode  : {'Run backup first' if summary.get('BackupExecuted') else 'Latest restore point'}",
        f"Overall      : {status}",
        "",
    ]
    for workload in WORKLOADS:
        item = summary.get(workload) or {}
        st = normalize_status(item.get("Status"), "N/A")
        loc = item.get("LocalFile")
        if loc and Path(loc).is_file():
            lines.append(f"{workload:12}: {st} (File: {Path(loc).name})")
        else:
            lines.append(f"{workload:12}: {st}")
    lines.extend(["", "PER-JOB RESULTS"])
    for entry in summary.get("JobResults", []):
        org_prefix = f"[{entry.get('organization')}] " if entry.get("organization") else ""
        rep = entry.get("report") if isinstance(entry.get("report"), dict) else {}
        passed = [wl for wl in WORKLOADS if isinstance(rep.get(wl), dict) and str(rep.get(wl, {}).get("Status", "")).upper() == "SUCCESS"]
        job_status = overall_status(rep) if rep else "FAILED"
        if entry.get("exit_code") != 0 and job_status == "SUCCESS":
            job_status = "WARNING"
        detail = f": {', '.join(passed)}" if passed else ""
        lines.append(f"- {org_prefix}{entry.get('job')}: {job_status}{detail} · exit {entry.get('exit_code')} ({entry.get('report_path') or 'no report'})")
    errors: list[str] = []
    if "txt" in selected_formats:
        try:
            (directory / "Report_Summary.txt").write_text("\n".join(lines), encoding="utf-8")
        except Exception as exc:
            errors.append(f"TXT: {exc}")
    rendered_summary = client_report_summary(summary)
    html_content = None
    if selected_formats & {"html", "pdf"}:
        try:
            from core.html_report import render_html_report
            html_content = render_html_report(rendered_summary)
            if "html" in selected_formats:
                (directory / "Report_Summary.html").write_text(html_content, encoding="utf-8")
        except Exception as exc:
            errors.append(f"HTML: {exc}")

    # Generate PDF version of the report
    if "pdf" in selected_formats:
        try:
            from core.pdf_report import write_pdf_report
            write_pdf_report(directory, rendered_summary, html_content=html_content)
        except Exception as exc:
            errors.append(f"PDF: {exc}")

    summary["ReportErrors"] = errors
    summary["ReportDurationSeconds"] = round(monotonic() - report_started, 3)
    temporary = json_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(json_path)

    # Enforce retention policy on the org directory
    if org_dir and org_dir.is_dir():
        from core.retention import enforce_retention
        enforce_retention(org_dir, max_keep)

    return json_path
