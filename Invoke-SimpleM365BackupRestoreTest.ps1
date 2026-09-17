#requires -Version 7.0
<#
.SYNOPSIS
    Script di Backup e Test di Restore Locale (2 Fasi) per Veeam Backup for Microsoft 365.

.DESCRIPTION
    Fase 1: Convalida e acquisisce i parametri del Tenant/Organizzazione e del Job di backup,
            connettendosi al server Veeam Backup for Microsoft 365 (VB365).
    Fase 2: Avvia il Job di backup e ne attende il completamento. Al termine del backup,
            apre l'ultimo restore point ed estrae (restore su macchina locale VB365)
            un campione casuale per ciascun workload:
              - 1 email casuale da Exchange (esportata in formato .msg)
              - 1 file casuale da OneDrive (salvato su disco locale)
              - 1 file casuale da SharePoint (salvato su disco locale)
            Infine, verifica la presenza e l'integrità dei file estratti (size > 0, hash SHA-256)
            e stampa un report conclusivo a console salvandolo anche su file.

.PARAMETER OrganizationName
    Nome del Tenant/Organizzazione M365 registrata in Veeam (es. israk.onmicrosoft.com).

.PARAMETER JobName
    Nome del Job di backup in Veeam (es. VB365-LAB-M365-Backup).

.PARAMETER LocalRestoreRoot
    Cartella sulla macchina VB365 dove copiare/salvare gli elementi estratti.
    Default: C:\VeeamRestoreLocalTest

.PARAMETER SkipBackup
    Switch facoltativo per testare direttamente la fase di estrazione/restore sull'ultimo restore point già presente.

.EXAMPLE
    .\Invoke-SimpleM365BackupRestoreTest.ps1 -OrganizationName "israk.onmicrosoft.com" -JobName "VB365-LAB-M365-Backup"
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$OrganizationName,

    [Parameter(Position = 1)]
    [string]$JobName,

    [Parameter(Position = 2)]
    [string]$LocalRestoreRoot = "C:\VeeamRestoreLocalTest",

    [switch]$SkipBackup
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Funzioni di Log e Formattazione
# ---------------------------------------------------------------------------
function Write-StepHeader {
    param([string]$Message)
    Write-Host "`n========================================================" -ForegroundColor Cyan
    Write-Host "  $Message" -ForegroundColor Yellow
    Write-Host "========================================================" -ForegroundColor Cyan
}

function Write-InfoLog {
    param([string]$Message)
    Write-Host "[INFO] $Message" -ForegroundColor White
}

function Write-SuccessLog {
    param([string]$Message)
    Write-Host "[OK]   $Message" -ForegroundColor Green
}

function Write-WarnLog {
    param([string]$Message)
    Write-Host "[WARN] $Message" -ForegroundColor DarkYellow
}

function Write-ErrorLog {
    param([string]$Message)
    Write-Host "[ERR]  $Message" -ForegroundColor Red
}

# ===========================================================================
# FASE 1: RACCOLTA DATI DEL TENANT E VERIFICA CONNESSIONE VEEAM
# ===========================================================================
Write-StepHeader "FASE 1: ACQUISIZIONE DATI TENANT E PREFLIGHT CHECK"

if ([string]::IsNullOrWhiteSpace($OrganizationName)) {
    $OrganizationName = Read-Host "Inserisci il nome del Tenant / Organizzazione (es. israk.onmicrosoft.com)"
}
if ([string]::IsNullOrWhiteSpace($OrganizationName)) {
    Write-ErrorLog "Nome Organizzazione obbligatorio. Uscita."
    exit 1
}

if ([string]::IsNullOrWhiteSpace($JobName)) {
    $JobName = Read-Host "Inserisci il nome del Job di Backup (es. VB365-LAB-M365-Backup)"
}
if ([string]::IsNullOrWhiteSpace($JobName)) {
    Write-ErrorLog "Nome Job di backup obbligatorio. Uscita."
    exit 1
}

if (-not (Test-Path -Path $LocalRestoreRoot)) {
    try {
        New-Item -ItemType Directory -Path $LocalRestoreRoot -Force | Out-Null
        Write-InfoLog "Creata cartella root di destinazione: $LocalRestoreRoot"
    } catch {
        Write-WarnLog "Impossibile creare $LocalRestoreRoot, uso cartella temporanea locale C:\Temp\VeeamRestoreLocalTest"
        $LocalRestoreRoot = "C:\Temp\VeeamRestoreLocalTest"
        New-Item -ItemType Directory -Path $LocalRestoreRoot -Force | Out-Null
    }
}

$runTimestamp = (Get-Date).ToString("yyyyMMdd_HHmmss")
$runDirectory = Join-Path $LocalRestoreRoot $runTimestamp
New-Item -ItemType Directory -Path $runDirectory -Force | Out-Null

Write-InfoLog "Cartella locale di estrazione per questo run: $runDirectory"

Write-InfoLog "Inizializzazione moduli Veeam Backup for Microsoft 365..."
$requiredModules = @('Veeam.Archiver.PowerShell')
foreach ($mod in $requiredModules) {
    if (Get-Module -ListAvailable -Name $mod) {
        Import-Module $mod -ErrorAction SilentlyContinue
    }
}

try {
    Write-InfoLog "Connessione al server Veeam VB365 locale..."
    Connect-VBOServer -Server "localhost" -ErrorAction Stop | Out-Null
    Write-SuccessLog "Connesso con successo al server Veeam Backup for Microsoft 365."
} catch {
    Write-ErrorLog "Errore durante la connessione al server Veeam: $($_.Exception.Message)"
    Write-ErrorLog "Assicurati di eseguire lo script nella 'Veeam Backup for Microsoft 365 PowerShell' con privilegi di amministratore."
    exit 2
}

try {
    Write-InfoLog "Verifica presenza organizzazione '$OrganizationName'..."
    $org = Get-VBOOrganization -Name $OrganizationName -ErrorAction SilentlyContinue
    if ($null -eq $org) {
        Write-ErrorLog "Organizzazione '$OrganizationName' non trovata su questo server Veeam."
        Disconnect-VBOServer
        exit 2
    }
    Write-SuccessLog "Organizzazione trovata: $($org.Name) (ID: $($org.Id))"

    Write-InfoLog "Verifica presenza Job di backup '$JobName'..."
    $job = Get-VBOJob -Organization $org -Name $JobName -ErrorAction SilentlyContinue
    if ($null -eq $job) {
        $job = Get-VBOJob -Name $JobName -ErrorAction SilentlyContinue
    }
    if ($null -eq $job) {
        Write-ErrorLog "Job di backup '$JobName' non trovato."
        Disconnect-VBOServer
        exit 2
    }
    Write-SuccessLog "Job di backup trovato: $($job.Name) (ID: $($job.Id))"
} catch {
    Write-ErrorLog "Errore durante la convalida dell'organizzazione o del job: $($_.Exception.Message)"
    Disconnect-VBOServer
    exit 2
}

# ===========================================================================
# FASE 2: ESECUZIONE BACKUP E RESTORE (COPIA LOCALE)
# ===========================================================================
Write-StepHeader "FASE 2: ESECUZIONE BACKUP E RESTORE (COPIA LOCALE)"

$results = [ordered]@{
    RunTimestamp     = $runTimestamp
    Organization     = $OrganizationName
    JobName          = $JobName
    BackupExecuted   = (-not $SkipBackup)
    BackupStatus     = "Skipped"
    RestorePointDate = "N/A"
    Exchange         = $null
    OneDrive         = $null
    SharePoint       = $null
    AllSuccessful    = $false
}

try {
    # 2.1 Esecuzione del Job di Backup
    if (-not $SkipBackup) {
        Write-InfoLog "Avvio del Job di Backup '$JobName'..."
        $startUtc = [DateTime]::UtcNow
        $startedSession = Start-VBOJob -Job $job
        Write-InfoLog "Job avviato. In attesa del completamento del backup..."
        
        $backupCompleted = $false
        $finalStatus = "Unknown"
        while (-not $backupCompleted) {
            Start-Sleep -Seconds 5
            $session = Get-VBOJobSession -Job $job -Last
            if ($null -ne $session) {
                $status = [string]$session.Status
                $progress = [string]$session.Progress
                Write-InfoLog "Avanzamento backup: $status (Progresso: $progress)"
                
                if ($status -in @('Success', 'Warning')) {
                    $backupCompleted = $true
                    $finalStatus = $status
                    break
                }
                if ($status -in @('Failed', 'Stopped')) {
                    throw "Il Job di backup si e concluso con stato: $status"
                }
            }
        }
        
        $results.BackupStatus = $finalStatus
        Write-SuccessLog "Backup completato con successo (Stato: $finalStatus)!"
    } else {
        Write-WarnLog "Salto esecuzione backup su richiesta (-SkipBackup). Procedo con l'ultimo restore point disponibile."
        $results.BackupStatus = "SkippedByUser"
    }

    # 2.2 Recupero dell'ultimo restore point valido
    Write-InfoLog "Recupero dell'ultimo Restore Point per il job '$JobName'..."
    $restorePoint = Get-VBORestorePoint -Job $job -Latest
    if ($null -eq $restorePoint) {
        $allPoints = @(Get-VBORestorePoint -Organization $org)
        if ($allPoints.Count -gt 0) {
            $restorePoint = $allPoints | Sort-Object -Property BackupTime | Select-Object -Last 1
        }
    }
    if ($null -eq $restorePoint) {
        throw "Nessun Restore Point trovato per l'organizzazione o job specificato."
    }

    $pointDate = $restorePoint.BackupTime
    if ($null -eq $pointDate) {
        $pointDate = "Presente"
    }
    $results.RestorePointDate = [string]$pointDate
    Write-SuccessLog "Restore Point individuato: $($restorePoint.Id) (Data: $($results.RestorePointDate))"

    # -----------------------------------------------------------------------
    # 2.3 RESTORE EXCHANGE -> Esportazione locale di 1 email casuale (.msg)
    # -----------------------------------------------------------------------
    Write-InfoLog "`n--- [1/3] Estrazione Campione Exchange Online ---"
    $exDir = Join-Path $runDirectory "Exchange"
    New-Item -ItemType Directory -Path $exDir -Force | Out-Null

    $exSession = $null
    try {
        Write-InfoLog "Apertura sessione Explorer per Exchange..."
        $exSession = Start-VBOExchangeItemRestoreSession -RestorePoint $restorePoint
        
        $databases = @(Get-VEXDatabase -Session $exSession)
        $mailboxes = @(
            foreach ($db in $databases) {
                Get-VEXMailbox -Database $db
            }
        )
        
        $filteredMailboxes = @()
        foreach ($mb in $mailboxes) {
            $mbEmail = if ($null -ne $mb.Email) { [string]$mb.Email } else { [string]$mb.Name }
            if ($mbEmail -notlike "*Discovery*" -and $mbEmail -notlike "*RestoreTest*" -and -not $mb.IsDeleted) {
                $filteredMailboxes += $mb
            }
        }

        if ($filteredMailboxes.Count -eq 0) {
            throw "Nessuna casella postale utente idonea trovata nel restore point."
        }

        $selectedMailbox = $null
        $selectedEmail = $null
        
        $shuffledMailboxes = $filteredMailboxes | Get-Random -Count $filteredMailboxes.Count
        foreach ($mb in $shuffledMailboxes) {
            $items = @(Get-VEXItem -Mailbox $mb | Where-Object { 
                $class = [string]$_.ItemClass
                $class -like "IPM.Note*" -or $class -eq ""
            })
            if ($items.Count -gt 0) {
                $selectedMailbox = $mb
                $selectedEmail = $items | Get-Random -Count 1
                break
            }
        }

        if ($null -eq $selectedEmail) {
            throw "Nessun messaggio email trovato nelle caselle postali disponibili."
        }

        $mbName = if ($null -ne $selectedMailbox.Email) { [string]$selectedMailbox.Email } else { [string]$selectedMailbox.Name }
        Write-InfoLog "Mail casuale selezionata:"
        Write-InfoLog "  - Casella sorgente : $mbName"
        Write-InfoLog "  - Oggetto          : $($selectedEmail.Subject)"
        Write-InfoLog "  - Data invio       : $($selectedEmail.Sent)"

        Write-InfoLog "Esportazione messaggio in locale (Export-VEXItem)..."
        Export-VEXItem -Item $selectedEmail -To $exDir -Force | Out-Null

        $exportedMsgFiles = @(Get-ChildItem -Path $exDir -File -Recurse)
        if ($exportedMsgFiles.Count -gt 0 -and $exportedMsgFiles[0].Length -gt 0) {
            $file = $exportedMsgFiles[0]
            $hash = (Get-FileHash -Path $file.FullName -Algorithm SHA256).Hash
            $results.Exchange = [ordered]@{
                Status       = "SUCCESS"
                SourceMailbox= $mbName
                Subject      = $selectedEmail.Subject
                LocalFile    = $file.FullName
                SizeBytes    = $file.Length
                SHA256       = $hash
            }
            Write-SuccessLog "Email esportata e verificata con successo: $($file.Name) ($($file.Length) bytes, SHA256: $($hash.Substring(0,16))...)"
        } else {
            throw "Il file esportato da Exchange non e presente o ha dimensione pari a 0 byte."
        }
    } catch {
        Write-ErrorLog "Errore estrazione Exchange: $($_.Exception.Message)"
        $results.Exchange = [ordered]@{
            Status = "FAILED"
            Error  = $_.Exception.Message
        }
    } finally {
        if ($null -ne $exSession) {
            try { Stop-VBOExchangeItemRestoreSession -Session $exSession } catch {}
            Write-InfoLog "Sessione Exchange chiusa regolarmente."
        }
    }

    # -----------------------------------------------------------------------
    # 2.4 RESTORE ONEDRIVE -> Salvataggio su disco locale di 1 file casuale
    # -----------------------------------------------------------------------
    Write-InfoLog "`n--- [2/3] Estrazione Campione OneDrive for Business ---"
    $odDir = Join-Path $runDirectory "OneDrive"
    New-Item -ItemType Directory -Path $odDir -Force | Out-Null

    $odSession = $null
    try {
        Write-InfoLog "Apertura sessione Explorer per OneDrive..."
        $odSession = Start-VEODRestoreSession -RestorePoint $restorePoint
        
        $odUsers = @(Get-VEODUser -Session $odSession)
        $filteredUsers = @()
        foreach ($u in $odUsers) {
            $uName = if ($null -ne $u.Name) { [string]$u.Name } else { [string]$u.UserName }
            if ($uName -notlike "*RestoreTest*") {
                $filteredUsers += $u
            }
        }

        if ($filteredUsers.Count -eq 0) {
            throw "Nessun utente OneDrive trovato nel restore point."
        }

        $selectedOdUser = $null
        $selectedDoc = $null

        $shuffledOdUsers = $filteredUsers | Get-Random -Count $filteredUsers.Count
        foreach ($u in $shuffledOdUsers) {
            $docs = @(Get-VEODDocument -User $u -Recurse | Where-Object { 
                $name = [string]$_.Name
                (-not [string]::IsNullOrWhiteSpace($name)) -and [IO.Path]::HasExtension($name)
            })
            if ($docs.Count -gt 0) {
                $selectedOdUser = $u
                $selectedDoc = $docs | Get-Random -Count 1
                break
            }
        }

        if ($null -eq $selectedDoc) {
            throw "Nessun documento valido trovato nei profili OneDrive esaminati."
        }

        $odUserName = if ($null -ne $selectedOdUser.Name) { [string]$selectedOdUser.Name } else { [string]$selectedOdUser.UserName }
        Write-InfoLog "Documento OneDrive casuale selezionato:"
        Write-InfoLog "  - Utente sorgente  : $odUserName"
        Write-InfoLog "  - Nome file        : $($selectedDoc.Name)"

        Write-InfoLog "Salvataggio documento in locale (Save-VEODDocument)..."
        Save-VEODDocument -Document $selectedDoc -Path $odDir | Out-Null

        $savedOdFiles = @(Get-ChildItem -Path $odDir -File -Recurse)
        if ($savedOdFiles.Count -gt 0 -and $savedOdFiles[0].Length -gt 0) {
            $file = $savedOdFiles[0]
            $hash = (Get-FileHash -Path $file.FullName -Algorithm SHA256).Hash
            $results.OneDrive = [ordered]@{
                Status    = "SUCCESS"
                SourceUser= $odUserName
                FileName  = $selectedDoc.Name
                LocalFile = $file.FullName
                SizeBytes = $file.Length
                SHA256    = $hash
            }
            Write-SuccessLog "Documento OneDrive salvato e verificato con successo: $($file.Name) ($($file.Length) bytes, SHA256: $($hash.Substring(0,16))...)"
        } else {
            throw "Il file OneDrive salvato localmente non e presente o ha dimensione pari a 0 byte."
        }
    } catch {
        Write-ErrorLog "Errore estrazione OneDrive: $($_.Exception.Message)"
        $results.OneDrive = [ordered]@{
            Status = "FAILED"
            Error  = $_.Exception.Message
        }
    } finally {
        if ($null -ne $odSession) {
            try { Stop-VEODRestoreSession -Session $odSession } catch {}
            Write-InfoLog "Sessione OneDrive chiusa regolarmente."
        }
    }

    # -----------------------------------------------------------------------
    # 2.5 RESTORE SHAREPOINT -> Salvataggio su disco locale di 1 doc casuale
    # -----------------------------------------------------------------------
    Write-InfoLog "`n--- [3/3] Estrazione Campione SharePoint Online ---"
    $spDir = Join-Path $runDirectory "SharePoint"
    New-Item -ItemType Directory -Path $spDir -Force | Out-Null

    $spSession = $null
    try {
        Write-InfoLog "Apertura sessione Explorer per SharePoint..."
        $spSession = Start-VBOSharePointItemRestoreSession -RestorePoint $restorePoint
        
        $spOrgs = @(Get-VESPOrganization -Session $spSession)
        $spSites = @(
            foreach ($spO in $spOrgs) {
                Get-VESPSite -Organization $spO -Recurse | Where-Object { $_.Name -notlike "*RestoreTest*" }
            }
        )
        
        if ($spSites.Count -eq 0) {
            throw "Nessun sito SharePoint valido trovato nel restore point."
        }

        $selectedSite = $null
        $selectedLibrary = $null
        $selectedSpDoc = $null

        $shuffledSites = $spSites | Get-Random -Count $spSites.Count
        foreach ($site in $shuffledSites) {
            $libs = @(Get-VESPDocumentLibrary -Site $site -Recurse | Where-Object { 
                $_.Name -notmatch "SitePages|Site Assets|Style Library|Form Templates|User Photos" 
            })
            foreach ($lib in $libs) {
                $docs = @(Get-VESPDocument -DocumentLibrary $lib -Recurse | Where-Object { 
                    $docName = [string]$_.Name
                    (-not [string]::IsNullOrWhiteSpace($docName)) -and 
                    ($docName -notmatch '\.(aspx|master|html?)$')
                })
                if ($docs.Count -gt 0) {
                    $selectedSite = $site
                    $selectedLibrary = $lib
                    $selectedSpDoc = $docs | Get-Random -Count 1
                    break
                }
            }
            if ($null -ne $selectedSpDoc) { break }
        }

        if ($null -eq $selectedSpDoc) {
            throw "Nessun documento valido trovato nei siti e librerie SharePoint analizzate."
        }

        Write-InfoLog "Documento SharePoint casuale selezionato:"
        Write-InfoLog "  - Sito sorgente    : $($selectedSite.Name)"
        Write-InfoLog "  - Document Library : $($selectedLibrary.Name)"
        Write-InfoLog "  - Nome file        : $($selectedSpDoc.Name)"

        Write-InfoLog "Salvataggio documento in locale (Save-VESPItem)..."
        Save-VESPItem -Document $selectedSpDoc -Path $spDir -Force | Out-Null

        $savedSpFiles = @(Get-ChildItem -Path $spDir -File -Recurse)
        if ($savedSpFiles.Count -gt 0 -and $savedSpFiles[0].Length -gt 0) {
            $file = $savedSpFiles[0]
            $hash = (Get-FileHash -Path $file.FullName -Algorithm SHA256).Hash
            $results.SharePoint = [ordered]@{
                Status    = "SUCCESS"
                Site      = $selectedSite.Name
                Library   = $selectedLibrary.Name
                FileName  = $selectedSpDoc.Name
                LocalFile = $file.FullName
                SizeBytes = $file.Length
                SHA256    = $hash
            }
            Write-SuccessLog "Documento SharePoint salvato e verificato con successo: $($file.Name) ($($file.Length) bytes, SHA256: $($hash.Substring(0,16))...)"
        } else {
            throw "Il file SharePoint salvato localmente non e presente o ha dimensione pari a 0 byte."
        }
    } catch {
        Write-ErrorLog "Errore estrazione SharePoint: $($_.Exception.Message)"
        $results.SharePoint = [ordered]@{
            Status = "FAILED"
            Error  = $_.Exception.Message
        }
    } finally {
        if ($null -ne $spSession) {
            try { Stop-VBOSharePointItemRestoreSession -Session $spSession } catch {}
            Write-InfoLog "Sessione SharePoint chiusa regolarmente."
        }
    }

} catch {
    Write-ErrorLog "Errore critico durante l'esecuzione del processo: $($_.Exception.Message)"
} finally {
    Disconnect-VBOServer
    Write-InfoLog "Disconnessione dal server Veeam completata."
}

$exOk = ($null -ne $results.Exchange -and $results.Exchange.Status -eq "SUCCESS")
$odOk = ($null -ne $results.OneDrive -and $results.OneDrive.Status -eq "SUCCESS")
$spOk = ($null -ne $results.SharePoint -and $results.SharePoint.Status -eq "SUCCESS")
$backupOk = ($results.BackupStatus -in @("Success", "Warning", "SkippedByUser"))

$allPassed = ($exOk -and $odOk -and $spOk -and $backupOk)
$results.AllSuccessful = $allPassed

$jsonReportPath = Join-Path $runDirectory "Report_Summary.json"
$results | ConvertTo-Json -Depth 6 | Set-Content -Path $jsonReportPath -Encoding UTF8

$txtReportPath = Join-Path $runDirectory "Report_Summary.txt"
$reportContent = @"
======================================================================
     REPORT CONCLUSIVO: TEST BACKUP & RESTORE LOCALE VEEAM M365
======================================================================
Data/Ora Run      : $(Get-Date -Format 'dd/MM/yyyy HH:mm:ss')
Organizzazione    : $($results.Organization)
Job di Backup     : $($results.JobName)
Esito Backup      : $($results.BackupStatus)
Restore Point Usato : $($results.RestorePointDate)
Cartella Locale   : $runDirectory

----------------------------------------------------------------------
DETTAGLIO RESTORE CAMPIONI (COPIA SU PUNTO LOCALE MACCHINA VEEAM):
----------------------------------------------------------------------
[1] EXCHANGE ONLINE (EMAIL)
    - Esito       : $(if ($exOk) { "SUCCESSO" } else { "FALLITO" })
    - Casella     : $(if ($exOk) { $results.Exchange.SourceMailbox } else { "N/A" })
    - Oggetto     : $(if ($exOk) { $results.Exchange.Subject } else { "N/A" })
    - File Locale : $(if ($exOk) { $results.Exchange.LocalFile } else { "N/A" })
    - Dimensione  : $(if ($exOk) { "$($results.Exchange.SizeBytes) bytes" } else { "N/A" })
    - Checksum    : $(if ($exOk) { $results.Exchange.SHA256 } else { "N/A" })

[2] ONEDRIVE FOR BUSINESS (FILE)
    - Esito       : $(if ($odOk) { "SUCCESSO" } else { "FALLITO" })
    - Utente      : $(if ($odOk) { $results.OneDrive.SourceUser } else { "N/A" })
    - File        : $(if ($odOk) { $results.OneDrive.FileName } else { "N/A" })
    - File Locale : $(if ($odOk) { $results.OneDrive.LocalFile } else { "N/A" })
    - Dimensione  : $(if ($odOk) { "$($results.OneDrive.SizeBytes) bytes" } else { "N/A" })
    - Checksum    : $(if ($odOk) { $results.OneDrive.SHA256 } else { "N/A" })

[3] SHAREPOINT ONLINE (DOCUMENTO)
    - Esito       : $(if ($spOk) { "SUCCESSO" } else { "FALLITO" })
    - Sito / Lib  : $(if ($spOk) { "$($results.SharePoint.Site) / $($results.SharePoint.Library)" } else { "N/A" })
    - Documento   : $(if ($spOk) { $results.SharePoint.FileName } else { "N/A" })
    - File Locale : $(if ($spOk) { $results.SharePoint.LocalFile } else { "N/A" })
    - Dimensione  : $(if ($spOk) { "$($results.SharePoint.SizeBytes) bytes" } else { "N/A" })
    - Checksum    : $(if ($spOk) { $results.SharePoint.SHA256 } else { "N/A" })

======================================================================
ESITO COMPLESSIVO OPERAZIONE: $(if ($allPassed) { "TUTTO ANDATO A BUON FINE (SUCCESS)" } else { "PARZIALE O FALLITO" })
======================================================================
"@

$reportContent | Set-Content -Path $txtReportPath -Encoding UTF8

Write-Host "`n$reportContent" -ForegroundColor $(if ($allPassed) { "Green" } else { "Red" })
Write-InfoLog "Report salvati in:"
Write-InfoLog "  - TXT  : $txtReportPath"
Write-InfoLog "  - JSON : $jsonReportPath"

if ($allPassed) {
    exit 0
} else {
    exit 2
}
