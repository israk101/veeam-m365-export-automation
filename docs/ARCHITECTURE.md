# Architecture reference

This document is the maintainer-oriented reference for Veeam M365 Restore Tester 1.2.4. The operator guide and deployment procedure live in [`index.html`](index.html); the repository entry point is [`../README.md`](../README.md).

## Design goals

The implementation is organized around five constraints:

1. Keep Veeam automation in PowerShell, where the supported cmdlets and Explorer APIs are available.
2. Keep the Windows interface responsive during discovery, backups and restores.
3. Isolate each selected organization/job pair so evidence cannot be attributed to the wrong queued job.
4. Preserve an internal machine-readable record while generating simpler client-facing documents.
5. Package the application without requiring Python or a browser installation on the target Veeam host.

Python 3.12 and PySide6 provide the desktop shell. `QProcess` connects long-running PowerShell work to Qt's event loop, allowing line-by-line output and cancellation without blocking repainting or navigation. The PowerShell engine remains independently executable from the command line.

## System context

```text
Operator
   │
   ▼
PySide6 desktop application
   ├── reads/writes %APPDATA% settings
   ├── scans local report folders
   ├── starts PowerShell 7 child processes
   └── generates aggregate reports
             │
             ▼
Veeam.Archiver.PowerShell + Veeam Explorers
             │
             ├── local VB365 configuration and sessions
             ├── selected backup job (optional execution)
             └── latest restore point and item export
```

The application contains no tenant credential store. PowerShell connects to `localhost`; the local Veeam environment owns authentication and authorization.

## Runtime boundaries

### Inventory boundary

`core.discovery.InventoryDiscovery` starts:

```text
pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass
  -File Get-VeeamM365Inventory.ps1
```

The script connects to the local Veeam server, returns sorted organizations and jobs, and always attempts to disconnect in `finally`. Its last machine-readable line begins with `__VEEAM_INVENTORY__`. Python ignores surrounding informational output and accepts only JSON with an `Organizations` array.

### Restore boundary

For every queued pair, `core.runner.PowerShellRunner` starts:

```text
pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass
  -File <engine>
  -OrganizationName <organization>
  -JobName <job>
  -LocalRestoreRoot <isolated-job-directory>
  [-SkipBackup]
```

Arguments are passed as a `QProcess` argument list rather than interpolated into a shell command. Standard output and standard error are merged to preserve visible ordering. The GUI uses `[OK]`, `[WARN]` and `[ERR]` markers for display level and recognizes stable milestone text to update the current phase.

### File boundary

PowerShell communicates structured results through per-job `Report_Summary.json` files. Python accepts a result only when:

- it is found under the current job's isolated root;
- its modification time is not older than the job start (with a one-second filesystem tolerance); and
- its `JobName` equals the queued job name.

The final aggregator groups those job records by organization, creates one independent final directory for each group, copies representative evidence and writes the organization summary.

## Execution state machine

```text
IDLE
  │ Run selected jobs
  ▼
VALIDATE_INPUT ── failure ──► IDLE + dialog
  │
  ▼
BUILD_QUEUE
  │
  ▼
START_JOB ◄──────────────────────────┐
  │                                 │
  ▼                                 │
POWERSHELL_RUNNING                   │
  │ output/phase/timer               │
  ▼                                 │
COLLECT_JOB_RESULT ── more jobs ─────┘
  │ queue empty or stop requested
  ▼
BUILD_ORGANIZATION_SUMMARIES
  │
  ▼
WRITE_REPORTS → APPLY_RETENTION → CLEAN_STAGING → IDLE
```

The queue is intentionally sequential. Veeam Explorer restore sessions and the local VB365 service can be resource-heavy; serial execution removes cross-job session contention and makes cancellation/report attribution deterministic.

## Search and selection model

`ui.widgets.OrgJobTree` owns the organization/job hierarchy and check state. `filter_items()` performs a case-insensitive `casefold()` match over existing Qt items:

- an organization match shows the organization and all child jobs;
- a job match shows its parent and only matching child jobs;
- a blank query restores all rows;
- no item is rebuilt, so check state and inventory identity survive filtering.

`set_visible()` changes only currently visible job rows. Organization checkboxes are recomputed as checked, unchecked or partially checked from their children. `selected_jobs()` always walks the entire tree, including rows hidden by a filter; therefore a search is a view operation, not a destructive selection operation.

The persisted value `selected_multi_org_jobs` has this shape:

```json
{
  "contoso.onmicrosoft.com": ["Exchange Backup", "SharePoint Backup"],
  "fabrikam.onmicrosoft.com": ["Microsoft 365 Backup"]
}
```

It is used only to restore the previous checkbox selection after discovery. The authoritative inventory still comes from Veeam on each discovery.

## Component catalog

### Entry point and pages

| Component | Contract and responsibility |
|---|---|
| `main.py` | Creates `QApplication`, applies Fusion style, sets the application identity/icon/font and opens `MainWindow`. |
| `app.MainWindow` | Owns navigation, the shared `ConfigManager` and `PowerShellRunner`, page refresh and close protection while a process is active. |
| `app.DashboardPage` | Presents the newest final JSON report and workload summary; it does not run or mutate tests. |
| `app.RunPage` | Owns inventory state, filtering, checked jobs, queue state, timer, per-job lifecycle, aggregation and batch cleanup. |
| `app.ReportsPage` | Recursively loads accepted final JSON reports, displays status/duration/details and opens evidence or optional documents. |
| `app.SettingsPage` | Edits operational defaults: root, retention, skip-backup default, report formats and optional engine override. Organization/job selection deliberately remains in Run test. |

### Core services

| Component | Contract and responsibility |
|---|---|
| `core.config` | Whitelists supported settings, tolerates missing/invalid JSON and saves through a temporary file plus atomic replacement. |
| `core.paths` | Resolves source-tree versus PyInstaller resource paths, the bundled scripts and `pwsh.exe`. |
| `core.discovery` | Runs inventory asynchronously, extracts the marker payload and flattens it for the tree. |
| `core.runner` | Runs one engine process, streams text, classifies log levels, maps phases, terminates/kills on confirmed stop and emits the exit code. |
| `core.batch_report` | Normalizes statuses, groups per organization, selects representative workload evidence, writes JSON/TXT/HTML/PDF and triggers retention. |
| `core.reports` | Parses accepted JSON reports, sorts history by modification time and formats size/duration for UI. |
| `core.retention` | Sanitizes organization directory names and removes the oldest matching `RestoreTest_YYYYMMDD_HHMMSS` folders beyond the configured limit. |
| `core.html_report` | Builds a self-contained branded client HTML document with embedded images/styles. |
| `core.pdf_report` | Renders the same client model through Qt WebEngine/Chromium to an A4 PDF with a 30-second safety timeout. |

### UI services

| Component | Contract and responsibility |
|---|---|
| `ui.theme` | Central Qt stylesheet and color/spacing treatment. |
| `ui.widgets.PageHeading` | Consistent page title hierarchy. |
| `ui.widgets.StatusCard` | Workload state/details with text and icon, not color alone. |
| `ui.widgets.LogConsole` | Read-only colored live process output. |
| `ui.widgets.OrgJobTree` | Hierarchical inventory, parent/child check propagation, preserved filtering and selected-job serialization. |
| `ui.dialogs` | Branded information, warning, error and destructive-confirmation surfaces. |

### PowerShell services

| Component | Contract and responsibility |
|---|---|
| `scripts/Get-VeeamM365Inventory.ps1` | Read-only local organization/job inventory. |
| `scripts/Invoke-SimpleM365BackupRestoreTest.ps1` | Validates the requested org/job, optionally executes backup, opens the restore point, runs three workload samplers and writes a per-job report. |
| `scripts/RestoreSampleHelpers.ps1` | Rejects container objects, randomizes bounded candidates, exports in isolated attempt directories, validates non-zero files, avoids collisions and calculates SHA-256. |

## PowerShell verification pipeline

The engine performs these stages for one job:

1. Validate mandatory names and create a timestamped run directory.
2. Import `Veeam.Archiver.PowerShell` and connect to `localhost`.
3. Resolve the organization and job.
4. If `-SkipBackup` is absent, call `Start-VBOJob`, poll the started session and require an acceptable final state.
5. Obtain the latest restore point for the selected job. If the job has none, fail that queue item; never fall back to an organization-wide restore point that could belong to another job.
6. Open and close each relevant Explorer session in its own `try/finally` region.
7. Enumerate candidate mailboxes/users/sites/libraries while isolating individual enumeration errors.
8. Randomize candidates and attempt up to `MaxSampleAttempts` per workload.
9. Accept a sample only after a regular non-empty file has been copied and hashed.
10. Write per-job JSON/TXT and return an exit code representing the run outcome.

The three workload results include workload-specific metadata plus common fields such as `Status`, `LocalFile`, `SizeBytes`, `SHA256` or `Error` when applicable.

## Status and aggregation semantics

Canonical display/report statuses are:

- `SUCCESS`: validated usable evidence exists.
- `WARNING`: partial usable evidence exists or the aggregate needs attention.
- `FAILED`: no acceptable result was established or a hard failure occurred.
- `NOT CONFIGURED`: the workload is not present/protected for this job or restore point.

`normalize_status()` converts legacy Italian/alternate terms to the canonical English vocabulary. `overall_status()` first respects a valid explicit aggregate status, then evaluates workload and exit-code evidence. A per-job non-zero code is not treated in isolation when the structured report proves one or more successful workloads; this preserves partial evidence while keeping the aggregate visible as a warning when failures also exist.

The internal JSON model contains `DurationSeconds`; client TXT/HTML/PDF are generated from `client_report_summary()`, which removes that internal telemetry. This is a deliberate reporting boundary rather than a presentation-only hide.

## Report data model

The final JSON is the application's source of truth for history. Important fields:

| Field | Type | Meaning |
|---|---|---|
| `RunTimestamp` | string | Local batch start in `YYYYMMDD_HHMMSS`. |
| `Organization` | string | Final report organization. |
| `OrganizationsTested` | array | Normally one item in an organization-isolated final report. |
| `SelectedJobs` | array | Job names included in this organization summary. |
| `BackupExecuted` | boolean | Whether the queue ran backups before restore testing. |
| `JobResults` | array | Exit code, source report, duration and structured report per queued job. |
| `Exchange`, `OneDrive`, `SharePoint` | object | Representative workload outcome/evidence for the organization. |
| `AllSuccessful` | boolean | Strict aggregate success indicator. |
| `OverallStatus` | string | Canonical user-facing aggregate status. |
| `DurationSeconds` | integer | Sum of measured job durations for this organization; internal JSON/UI only. |

`copy_workload_artifacts()` selects the first available file for each successful representative workload, copies it into the final workload folder and rewrites `LocalFile` paths in the final model so links survive staging cleanup.

## Storage lifecycle

During execution:

```text
<restore-root>\Batch_<timestamp>\Jobs\<safe-org>\<safe-job>\...
```

At finalization:

```text
<restore-root>\<safe-org>\RestoreTest_<timestamp>\
```

The `Batch_*` workspace is removed only when the number of successfully written organization JSON paths equals the number of organization summaries. If cleanup fails, the run remains reviewable and the GUI logs a warning. Retention considers only names matching the exact completed-test pattern, reducing the chance that an unrelated directory is removed.

## Configuration contract

Supported keys are explicitly whitelisted:

| Key | Type | Default |
|---|---|---|
| `selected_multi_org_jobs` | object of string arrays | `{}` |
| `restore_root` | string | `C:\VeeamRestoreLocalTest` |
| `script_path` | string | empty, meaning bundled engine |
| `max_restore_tests` | integer | `5` |
| `skip_backups` | boolean | `true` |
| `report_formats` | string array | `txt`, `html`, `pdf` |

Unknown keys in older or manually edited configuration files are ignored. Invalid/missing JSON returns defaults rather than preventing startup. Writes are UTF-8 JSON and atomically replace the prior file.

## Failure and cancellation behavior

- Inventory failure disables job controls and surfaces the PowerShell error; it does not invent cached Veeam inventory.
- Failure to locate PowerShell or the configured script is rejected before a batch begins.
- A failed job is recorded and the queue continues to the next selected job unless the operator requested stop.
- Stop clears pending jobs, asks the current process to terminate, waits 2.5 seconds, then kills it if necessary.
- Missing/stale/mismatched per-job JSON is recorded as no report rather than attributed to the wrong queue item.
- Optional HTML/PDF generation errors do not suppress mandatory JSON output; operators should verify requested artifacts.
- Final staging cleanup happens only after final organization reports exist.

## Packaging

`build.ps1` invokes PyInstaller in `onedir`, windowed, UAC-admin mode and embeds three scripts plus visual assets. `version_info.txt` supplies Windows file/product metadata. The output contains a small launcher beside `_internal`, which holds Python, PySide6, Qt libraries, WebEngine/Chromium helpers, translations, scripts and assets.

The portable folder must remain intact. `onedir` was selected because Qt WebEngine is large; a one-file build would unpack the same runtime to a temporary directory on every launch and start considerably more slowly.

## Testing strategy

`tests/test_core.py` exercises pure data functions and Qt-facing behavior without requiring a Veeam server. It covers configuration, marker parsing, tree selection/filtering, process argument construction, aggregation/statuses, report formats, report history, retention, HTML/PDF paths, duration and cleanup.

The automated suite cannot prove Veeam cmdlet compatibility, permissions, protected workload availability or successful extraction on a specific production repository. Release acceptance therefore also requires a controlled Veeam host test that covers:

1. discovery with multiple organizations/jobs;
2. `-SkipBackup` on existing restore points;
3. a run with backup enabled in a safe test job;
4. all configured workloads, including an empty-folder repository case;
5. final JSON plus each enabled client format;
6. staging cleanup and retention;
7. opening evidence from Reports after application restart.

## Security and privacy considerations

- Restored evidence may contain customer data. Use restricted NTFS ACLs, protected transfer and an appropriate retention value.
- The custom script path is code execution under elevated application privileges; only trusted administrators should change it.
- `ExecutionPolicy Bypass` applies only to the child invocation and does not validate script provenance. Distribute trusted, hashed builds.
- No secrets are stored by the GUI, but report JSON can contain organization, job, user/site and local-path metadata.
- The application requests elevation at launch, so review source/build provenance before deployment.

## Architectural history

- **1.1:** introduced read-only inventory discovery and multi-job selection.
- **1.2:** added selectable reports, native dialogs and Qt WebEngine PDF output.
- **1.2.1:** hardened candidate filtering, non-zero validation, bounded retries and isolated Explorer errors.
- **1.2.2:** added monotonic duration and the internal/client report boundary with English statuses.
- **1.2.3:** made organization the final report/evidence isolation boundary and delayed staging cleanup until all summaries exist.
- **1.2.4:** added in-place organization/job filtering, visible-result selection actions, task-oriented Run test/Settings layouts and fail-safe job-bound restore-point resolution.

## Sources and standards

- [GitHub: Documentation done right](https://github.blog/developer-skills/documentation-done-right-a-developers-guide/)
- [Diátaxis documentation framework](https://diataxis.fr/)
- [Microsoft Windows app design principles](https://learn.microsoft.com/windows/apps/design/design-principles)
- [Microsoft inclusive software guidance](https://learn.microsoft.com/windows/apps/design/accessibility/designing-inclusive-software)
- [Qt for Python deployment tools](https://doc.qt.io/qtforpython-6/deployment/index.html)
- [PyInstaller operating mode](https://pyinstaller.org/en/stable/operating-mode.html)
- [Veeam Backup for Microsoft 365 PowerShell reference](https://helpcenter.veeam.com/docs/vbo365/powershell/get-vborestorepoint.html)
