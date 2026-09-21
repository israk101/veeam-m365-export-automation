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

.PARAMETER MaxSampleAttempts
    Numero massimo di elementi candidati da provare per ciascun workload quando
    un'esportazione fallisce o produce un file vuoto. Default: 25.

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

    [switch]$SkipBackup,

    [ValidateRange(1, 1000)]
    [int]$MaxSampleAttempts = 25
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

$sampleHelperPath = Join-Path $PSScriptRoot 'RestoreSampleHelpers.ps1'
if (-not (Test-Path -LiteralPath $sampleHelperPath)) {
    $sampleHelperPath = Join-Path $PSScriptRoot 'scripts\RestoreSampleHelpers.ps1'
}
if (-not (Test-Path -LiteralPath $sampleHelperPath)) {
    Write-ErrorLog "Helper di selezione campioni non trovato: RestoreSampleHelpers.ps1"
    exit 1
}
. $sampleHelperPath

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
        throw "Nessun Restore Point trovato per il job selezionato '$JobName'. Il test viene interrotto per evitare di usare il Restore Point di un altro job della stessa organizzazione."
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

        $exchangeResult = $null
        $exchangeAttempts = 0
        $exchangeCandidatesFound = 0
        $exchangeEnumerationErrors = 0
        $shuffledMailboxes = @($filteredMailboxes | Get-Random -Count $filteredMailboxes.Count)

        foreach ($mb in $shuffledMailboxes) {
            if ($exchangeAttempts -ge $MaxSampleAttempts) { break }

            try {
                $items = @(Get-VEXItem -Mailbox $mb -ErrorAction Stop | Where-Object {
                    $class = [string]$_.ItemClass
                    $class -like "IPM.Note*" -or $class -eq ""
                })
            } catch {
                $exchangeEnumerationErrors++
                Write-WarnLog "Impossibile enumerare la casella '$($mb.Name)': $($_.Exception.Message). Proseguo con un'altra casella."
                continue
            }

            if ($items.Count -eq 0) { continue }
            $mbName = if ($null -ne $mb.Email) { [string]$mb.Email } else { [string]$mb.Name }
            $candidates = @(
                foreach ($item in $items) {
                    [pscustomobject]@{ Item = $item; MailboxName = $mbName }
                }
            )
            $exchangeCandidatesFound += $candidates.Count
            $remainingAttempts = $MaxSampleAttempts - $exchangeAttempts
            $candidateResult = Invoke-VerifiedSampleExport `
                -Candidates $candidates `
                -DestinationRoot $exDir `
                -Workload 'Exchange' `
                -MaxAttempts $remainingAttempts `
                -ExportAction {
                    param($candidate, $attemptDirectory)
                    Export-VEXItem -Item $candidate.Item -To $attemptDirectory -Force -ErrorAction Stop | Out-Null
                }
            $exchangeAttempts += $candidateResult.Attempts
            if ($candidateResult.Success) {
                $exchangeResult = $candidateResult
                break
            }
        }

        if ($exchangeCandidatesFound -eq 0) {
            if ($exchangeEnumerationErrors -gt 0) {
                throw "Nessun messaggio Exchange enumerabile; $exchangeEnumerationErrors caselle hanno restituito errori."
            }
            throw "Nessun messaggio email trovato nelle caselle postali disponibili."
        }
        if ($null -eq $exchangeResult -or -not $exchangeResult.Success) {
            throw "Nessun messaggio Exchange esportabile e non vuoto trovato dopo $exchangeAttempts tentativi."
        }

        $selectedEmail = $exchangeResult.Candidate.Item
        $mbName = $exchangeResult.Candidate.MailboxName
        $file = $exchangeResult.File
        $hash = $exchangeResult.SHA256
        Write-InfoLog "Mail verificata selezionata:"
        Write-InfoLog "  - Casella sorgente : $mbName"
        Write-InfoLog "  - Oggetto          : $($selectedEmail.Subject)"
        Write-InfoLog "  - Data invio       : $($selectedEmail.Sent)"
        $results.Exchange = [ordered]@{
            Status        = "SUCCESS"
            SourceMailbox = $mbName
            Subject       = $selectedEmail.Subject
            LocalFile     = $file.FullName
            SizeBytes     = $file.Length
            SHA256        = $hash
            Attempts      = $exchangeAttempts
        }
        Write-SuccessLog "Email esportata e verificata con successo al tentativo $exchangeAttempts`: $($file.Name) ($($file.Length) bytes, SHA256: $($hash.Substring(0,16))...)"
    } catch {
        $errMsg = $_.Exception.Message
        $isNotPresent = (
            $errMsg -like "*does not contain any Exchange data*" -or
            $errMsg -like "*non contiene dati Exchange*" -or
            $errMsg -like "*Nessuna casella postale utente idonea trovata*" -or
            $errMsg -like "*Nessun messaggio email trovato*"
        )
        if ($isNotPresent) {
            Write-WarnLog "Carico di lavoro Exchange non presente o non configurato in questo restore point/job: $errMsg"
            $results.Exchange = [ordered]@{
                Status = "NOT_CONFIGURED"
                Error  = $errMsg
            }
        } else {
            Write-ErrorLog "Errore estrazione Exchange: $errMsg"
            $results.Exchange = [ordered]@{
                Status = "FAILED"
                Error  = $errMsg
            }
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

        $oneDriveResult = $null
        $oneDriveAttempts = 0
        $oneDriveCandidatesFound = 0
        $oneDriveEnumerationErrors = 0
        $shuffledOdUsers = @($filteredUsers | Get-Random -Count $filteredUsers.Count)

        foreach ($u in $shuffledOdUsers) {
            if ($oneDriveAttempts -ge $MaxSampleAttempts) { break }

            try {
                $docs = @(Get-VEODDocument -User $u -Recurse -ErrorAction Stop | Where-Object {
                    Test-RestoreDocumentCandidate -Item $_ -RequireExtension
                })
            } catch {
                $oneDriveEnumerationErrors++
                Write-WarnLog "Impossibile enumerare OneDrive per '$($u.Name)': $($_.Exception.Message). Proseguo con un altro utente."
                continue
            }

            if ($docs.Count -eq 0) { continue }
            $odUserName = if ($null -ne $u.Name) { [string]$u.Name } else { [string]$u.UserName }
            $candidates = @(
                foreach ($document in $docs) {
                    [pscustomobject]@{ Document = $document; UserName = $odUserName }
                }
            )
            $oneDriveCandidatesFound += $candidates.Count
            $remainingAttempts = $MaxSampleAttempts - $oneDriveAttempts
            $candidateResult = Invoke-VerifiedSampleExport `
                -Candidates $candidates `
                -DestinationRoot $odDir `
                -Workload 'OneDrive' `
                -MaxAttempts $remainingAttempts `
                -ExportAction {
                    param($candidate, $attemptDirectory)
                    Save-VEODDocument -Document $candidate.Document -Path $attemptDirectory -ErrorAction Stop | Out-Null
                }
            $oneDriveAttempts += $candidateResult.Attempts
            if ($candidateResult.Success) {
                $oneDriveResult = $candidateResult
                break
            }
        }

        if ($oneDriveCandidatesFound -eq 0) {
            if ($oneDriveEnumerationErrors -gt 0) {
                throw "Nessun documento OneDrive enumerabile; $oneDriveEnumerationErrors utenti hanno restituito errori."
            }
            throw "Nessun documento valido trovato nei profili OneDrive esaminati."
        }
        if ($null -eq $oneDriveResult -or -not $oneDriveResult.Success) {
            throw "Nessun documento OneDrive esportabile e non vuoto trovato dopo $oneDriveAttempts tentativi."
        }

        $selectedDoc = $oneDriveResult.Candidate.Document
        $odUserName = $oneDriveResult.Candidate.UserName
        $file = $oneDriveResult.File
        $hash = $oneDriveResult.SHA256
        Write-InfoLog "Documento OneDrive verificato selezionato:"
        Write-InfoLog "  - Utente sorgente  : $odUserName"
        Write-InfoLog "  - Nome file        : $($selectedDoc.Name)"
        $results.OneDrive = [ordered]@{
            Status     = "SUCCESS"
            SourceUser = $odUserName
            FileName   = $selectedDoc.Name
            LocalFile  = $file.FullName
            SizeBytes  = $file.Length
            SHA256     = $hash
            Attempts   = $oneDriveAttempts
        }
        Write-SuccessLog "Documento OneDrive salvato e verificato con successo al tentativo $oneDriveAttempts`: $($file.Name) ($($file.Length) bytes, SHA256: $($hash.Substring(0,16))...)"
    } catch {
        $errMsg = $_.Exception.Message
        $isNotPresent = (
            $errMsg -like "*does not contain any OneDrive data*" -or
            $errMsg -like "*non contiene dati OneDrive*" -or
            $errMsg -like "*Nessun utente OneDrive trovato*" -or
            $errMsg -like "*Nessun documento valido trovato nei profili OneDrive*"
        )
        if ($isNotPresent) {
            Write-WarnLog "Carico di lavoro OneDrive non presente o non configurato in questo restore point/job: $errMsg"
            $results.OneDrive = [ordered]@{
                Status = "NOT_CONFIGURED"
                Error  = $errMsg
            }
        } else {
            Write-ErrorLog "Errore estrazione OneDrive: $errMsg"
            $results.OneDrive = [ordered]@{
                Status = "FAILED"
                Error  = $errMsg
            }
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

        $sharePointResult = $null
        $sharePointAttempts = 0
        $sharePointCandidatesFound = 0
        $sharePointEnumerationErrors = 0
        $shuffledSites = @($spSites | Get-Random -Count $spSites.Count)

        foreach ($site in $shuffledSites) {
            if ($sharePointAttempts -ge $MaxSampleAttempts) { break }

            try {
                $libs = @(Get-VESPDocumentLibrary -Site $site -Recurse -ErrorAction Stop | Where-Object {
                    $_.Name -notmatch "SitePages|Site Assets|Style Library|Form Templates|User Photos"
                })
            } catch {
                $sharePointEnumerationErrors++
                Write-WarnLog "Impossibile enumerare le librerie del sito '$($site.Name)': $($_.Exception.Message). Proseguo con un altro sito."
                continue
            }

            if ($libs.Count -eq 0) { continue }
            $shuffledLibraries = @($libs | Get-Random -Count $libs.Count)
            foreach ($lib in $shuffledLibraries) {
                if ($sharePointAttempts -ge $MaxSampleAttempts) { break }

                try {
                    $docs = @(Get-VESPDocument -DocumentLibrary $lib -Recurse -ErrorAction Stop | Where-Object {
                        $docName = [string]$_.Name
                        (Test-RestoreDocumentCandidate -Item $_) -and
                        ($docName -notmatch '\.(aspx|master|html?)$')
                    })
                } catch {
                    $sharePointEnumerationErrors++
                    Write-WarnLog "Impossibile enumerare la libreria '$($lib.Name)' del sito '$($site.Name)': $($_.Exception.Message). Proseguo con un'altra libreria."
                    continue
                }

                if ($docs.Count -eq 0) { continue }
                $candidates = @(
                    foreach ($document in $docs) {
                        [pscustomobject]@{
                            Document = $document
                            Site     = $site
                            Library  = $lib
                        }
                    }
                )
                $sharePointCandidatesFound += $candidates.Count
                $remainingAttempts = $MaxSampleAttempts - $sharePointAttempts
                $candidateResult = Invoke-VerifiedSampleExport `
                    -Candidates $candidates `
                    -DestinationRoot $spDir `
                    -Workload 'SharePoint' `
                    -MaxAttempts $remainingAttempts `
                    -ExportAction {
                        param($candidate, $attemptDirectory)
                        Save-VESPItem -Document $candidate.Document -Path $attemptDirectory -Force -ErrorAction Stop | Out-Null
                    }
                $sharePointAttempts += $candidateResult.Attempts
                if ($candidateResult.Success) {
                    $sharePointResult = $candidateResult
                    break
                }
            }
            if ($null -ne $sharePointResult -and $sharePointResult.Success) { break }
        }

        if ($sharePointCandidatesFound -eq 0) {
            if ($sharePointEnumerationErrors -gt 0) {
                throw "Nessun documento SharePoint enumerabile; $sharePointEnumerationErrors siti o librerie hanno restituito errori."
            }
            throw "Nessun documento valido trovato nei siti e librerie SharePoint analizzate. Le cartelle e le pagine di sistema sono state escluse."
        }
        if ($null -eq $sharePointResult -or -not $sharePointResult.Success) {
            throw "Nessun documento SharePoint esportabile e non vuoto trovato dopo $sharePointAttempts tentativi."
        }

        $selectedSpDoc = $sharePointResult.Candidate.Document
        $selectedSite = $sharePointResult.Candidate.Site
        $selectedLibrary = $sharePointResult.Candidate.Library
        $file = $sharePointResult.File
        $hash = $sharePointResult.SHA256
        Write-InfoLog "Documento SharePoint verificato selezionato:"
        Write-InfoLog "  - Sito sorgente    : $($selectedSite.Name)"
        Write-InfoLog "  - Document Library : $($selectedLibrary.Name)"
        Write-InfoLog "  - Nome file        : $($selectedSpDoc.Name)"
        $results.SharePoint = [ordered]@{
            Status    = "SUCCESS"
            Site      = $selectedSite.Name
            Library   = $selectedLibrary.Name
            FileName  = $selectedSpDoc.Name
            LocalFile = $file.FullName
            SizeBytes = $file.Length
            SHA256    = $hash
            Attempts  = $sharePointAttempts
        }
        Write-SuccessLog "Documento SharePoint salvato e verificato con successo al tentativo $sharePointAttempts`: $($file.Name) ($($file.Length) bytes, SHA256: $($hash.Substring(0,16))...)"
    } catch {
        $errMsg = $_.Exception.Message
        $isNotPresent = (
            $errMsg -like "*does not contain any SharePoint data*" -or
            $errMsg -like "*non contiene dati SharePoint*" -or
            $errMsg -like "*Nessun sito SharePoint valido trovato*" -or
            $errMsg -like "*Nessun documento valido trovato nei siti e librerie SharePoint*"
        )
        if ($isNotPresent) {
            Write-WarnLog "Carico di lavoro SharePoint non presente o non configurato in questo restore point/job: $errMsg"
            $results.SharePoint = [ordered]@{
                Status = "NOT_CONFIGURED"
                Error  = $errMsg
            }
        } else {
            Write-ErrorLog "Errore estrazione SharePoint: $errMsg"
            $results.SharePoint = [ordered]@{
                Status = "FAILED"
                Error  = $errMsg
            }
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

$exFail = ($null -ne $results.Exchange -and $results.Exchange.Status -eq "FAILED")
$odFail = ($null -ne $results.OneDrive -and $results.OneDrive.Status -eq "FAILED")
$spFail = ($null -ne $results.SharePoint -and $results.SharePoint.Status -eq "FAILED")

$backupOk = ($results.BackupStatus -in @("Success", "Warning", "SkippedByUser"))

$hasSuccess = ($exOk -or $odOk -or $spOk)
$hasFailed = ($exFail -or $odFail -or $spFail)
$allPassed = ($hasSuccess -and (-not $hasFailed) -and $backupOk)
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
    - Esito       : $(if ($exOk) { "SUCCESS" } elseif ($null -ne $results.Exchange -and $results.Exchange.Status -eq "NOT_CONFIGURED") { "NOT CONFIGURED" } else { "FAILED" })
    - Casella     : $(if ($exOk) { $results.Exchange.SourceMailbox } else { "N/A" })
    - Oggetto     : $(if ($exOk) { $results.Exchange.Subject } else { "N/A" })
    - File Locale : $(if ($exOk) { $results.Exchange.LocalFile } else { "N/A" })
    - Dimensione  : $(if ($exOk) { "$($results.Exchange.SizeBytes) bytes" } else { "N/A" })
    - Checksum    : $(if ($exOk) { $results.Exchange.SHA256 } else { "N/A" })

[2] ONEDRIVE FOR BUSINESS (FILE)
    - Esito       : $(if ($odOk) { "SUCCESS" } elseif ($null -ne $results.OneDrive -and $results.OneDrive.Status -eq "NOT_CONFIGURED") { "NOT CONFIGURED" } else { "FAILED" })
    - Utente      : $(if ($odOk) { $results.OneDrive.SourceUser } else { "N/A" })
    - File        : $(if ($odOk) { $results.OneDrive.FileName } else { "N/A" })
    - File Locale : $(if ($odOk) { $results.OneDrive.LocalFile } else { "N/A" })
    - Dimensione  : $(if ($odOk) { "$($results.OneDrive.SizeBytes) bytes" } else { "N/A" })
    - Checksum    : $(if ($odOk) { $results.OneDrive.SHA256 } else { "N/A" })

[3] SHAREPOINT ONLINE (DOCUMENTO)
    - Esito       : $(if ($spOk) { "SUCCESS" } elseif ($null -ne $results.SharePoint -and $results.SharePoint.Status -eq "NOT_CONFIGURED") { "NOT CONFIGURED" } else { "FAILED" })
    - Sito / Lib  : $(if ($spOk) { "$($results.SharePoint.Site) / $($results.SharePoint.Library)" } else { "N/A" })
    - Documento   : $(if ($spOk) { $results.SharePoint.FileName } else { "N/A" })
    - File Locale : $(if ($spOk) { $results.SharePoint.LocalFile } else { "N/A" })
    - Dimensione  : $(if ($spOk) { "$($results.SharePoint.SizeBytes) bytes" } else { "N/A" })
    - Checksum    : $(if ($spOk) { $results.SharePoint.SHA256 } else { "N/A" })

======================================================================
ESITO COMPLESSIVO OPERAZIONE: $(if ($allPassed) { "SUCCESS" } elseif ($hasSuccess) { "WARNING" } else { "FAILED" })
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
