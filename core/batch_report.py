from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


WORKLOADS = ("Exchange", "OneDrive", "SharePoint")

WORKLOAD_RESTORE_FOLDERS = {
    "Exchange": "restore email",
    "OneDrive": "restore one drive",
    "SharePoint": "restore share point",
}


def copy_workload_artifacts(directory: Path, summary: dict[str, Any]) -> None:
    """Copy restored files from job run directories into the batch evidence folder."""
    import shutil

    for workload, folder_name in WORKLOAD_RESTORE_FOLDERS.items():
        candidates: list[str] = []
        item = summary.get(workload)
        if isinstance(item, dict) and item.get("LocalFile"):
            candidates.append(str(item["LocalFile"]))
        for entry in summary.get("JobResults", []):
            rep = entry.get("report")
            if isinstance(rep, dict):
                wl_data = rep.get(workload)
                if isinstance(wl_data, dict) and wl_data.get("LocalFile"):
                    candidates.append(str(wl_data["LocalFile"]))

        copied_path: Path | None = None
        for cand in candidates:
            src = Path(cand)
            if src.is_file():
                dest_dir = directory / folder_name
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_file = dest_dir / src.name
                if not dest_file.exists() or dest_file.stat().st_size != src.stat().st_size:
                    shutil.copy2(src, dest_file)
                copied_path = dest_file
                if src.parent.is_dir():
                    for sibling in src.parent.iterdir():
                        if sibling.is_file():
                            target = dest_dir / sibling.name
                            if not target.exists():
                                shutil.copy2(sibling, target)
                break

        if copied_path:
            new_path_str = str(copied_path)
            if isinstance(summary.get(workload), dict):
                summary[workload]["LocalFile"] = new_path_str
            for entry in summary.get("JobResults", []):
                rep = entry.get("report")
                if isinstance(rep, dict) and isinstance(rep.get(workload), dict):
                    if rep[workload].get("LocalFile"):
                        rep[workload]["LocalFile"] = new_path_str


def build_batch_summary(
    organization: str,
    jobs: list[dict[str, Any]],
    skip_backup: bool,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    now = timestamp or datetime.now()
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
            if rep.get("AllSuccessful"):
                return True
            statuses = [
                str(rep.get(wl, {}).get("Status", "")).upper()
                for wl in WORKLOADS
                if isinstance(rep.get(wl), dict)
            ]
            has_success = any(s == "SUCCESS" for s in statuses)
            has_failure = any(s in ("FAILED", "ERROR") for s in statuses)
            if has_success and not has_failure:
                return True
            if has_success and code in (0, 2):
                return True
        return code == 0

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
    return summary


def write_batch_report(
    root: str | Path,
    summary: dict[str, Any],
    max_keep: int = 5,
    report_formats: list[str] | tuple[str, ...] | set[str] | None = None,
) -> Path:
    selected_formats = {str(item).lower() for item in (report_formats or ("txt", "html", "pdf"))}
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

    directory.mkdir(parents=True, exist_ok=True)
    copy_workload_artifacts(directory, summary)
    json_path = directory / "Report_Summary.json"
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    status = "SUCCESS" if summary.get("AllSuccessful") else "NEEDS ATTENTION"
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
        st = item.get("Status", "N/A")
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
        tag = f" [SUCCESS: {', '.join(passed)}]" if passed else (" [SUCCESS]" if entry.get("exit_code") == 0 else "")
        lines.append(f"- {org_prefix}{entry.get('job')}: exit {entry.get('exit_code')}{tag} ({entry.get('report_path') or 'no report'})")
    if "txt" in selected_formats:
        (directory / "Report_Summary.txt").write_text("\n".join(lines), encoding="utf-8")
    if "html" in selected_formats:
        try:
            from core.html_report import write_html_report
            write_html_report(directory, summary)
        except Exception:
            pass

    # Generate PDF version of the report
    if "pdf" in selected_formats:
        try:
            from core.pdf_report import write_pdf_report
            write_pdf_report(directory, summary)
        except Exception:
            pass

    # Enforce retention policy on the org directory
    if org_dir and org_dir.is_dir():
        from core.retention import enforce_retention
        enforce_retention(org_dir, max_keep)

    return json_path
