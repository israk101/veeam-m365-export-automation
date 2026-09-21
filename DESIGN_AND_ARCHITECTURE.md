# Design and architecture notes

## Architecture decision

The application uses Python 3.12 and PySide6. Qt's native widget model provides a
stronger base for keyboard navigation, DPI scaling, accessible names and focus,
and future report views than a canvas-heavy or heavily custom Tk interface.
`QProcess` integrates PowerShell with the GUI event loop, so output can stream
without blocking the application.

The app deliberately wraps the simple PowerShell script unchanged. That keeps
the automation engine independently testable and makes it possible to use a
custom script later through Settings.

Version 1.1 added a separate read-only inventory helper. It connects to the
local VB365 server, returns organizations with their jobs as JSON, and then
disconnects. The GUI never modifies Veeam configuration during discovery.

For multi-job runs, the GUI invokes the unchanged restore engine once per
selected job. Each invocation receives an isolated evidence root beneath one
batch directory. A Python aggregator then selects the successful Exchange,
OneDrive, and SharePoint evidence across those job reports and writes a combined
`Report_Summary.json` at the batch root, plus the user-selected TXT, HTML and
PDF documents. JSON remains mandatory because it drives Dashboard and Reports.

Version 1.2 adds selectable report formats, application-native dialogs and a
PDF executive report generated through Qt WebEngine. The WebEngine dependency
is packaged into the standalone executable so the target host does not need a
separate browser or Python installation.

Version 1.2.1 hardens sample extraction for repositories containing empty
folders, zero-byte items, stale objects or isolated Explorer errors. Folder
containers are rejected through `IsContainer`/`IsFolder`; each candidate is
exported into an isolated temporary directory and is accepted only after a
non-zero size check, collision-safe copy and SHA-256 calculation. Failed
candidates are retried up to a bounded per-workload limit.

## UX decisions

- Four flat destinations: Dashboard, Run test, Reports, Settings
- Startup organization discovery with an explicit refresh action
- Job selection uses visible checkboxes and Select all/Clear controls
- One primary action per view
- Status uses text and symbols in addition to color
- Evidence stays visible and user-controlled on local storage
- Destructive interruption requires an explicit confirmation
- UAC elevation occurs when the packaged app launches, before a long run begins

The visual language follows Microsoft's guidance for calm layouts, clear
hierarchy, standard controls, limited navigation depth, and keyboard-accessible
interactions.

## Process boundary

The application invokes:

```text
pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass
  -File <script>
  -OrganizationName <tenant>
  -JobName <job>
  -LocalRestoreRoot <folder>
  [-SkipBackup]
```

The GUI never embeds tenant credentials. Veeam connectivity remains inside the
existing local PowerShell/VB365 environment.

## Sources consulted

- Microsoft Windows app design principles:
  https://learn.microsoft.com/windows/apps/design/design-principles
- Microsoft Windows app navigation basics:
  https://learn.microsoft.com/windows/apps/design/basics/navigation-basics
- Microsoft inclusive Windows design guidance:
  https://learn.microsoft.com/windows/apps/design/accessibility/designing-inclusive-software
- Qt for Python deployment tools:
  https://doc.qt.io/qtforpython-6/tools/index.html
- PyInstaller documentation:
  https://pyinstaller.org/en/stable/
