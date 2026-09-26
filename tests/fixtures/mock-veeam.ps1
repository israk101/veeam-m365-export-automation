# Offline integration fixture. All Veeam entry points used by the engine are mocked.
$global:counts = @{ Exchange = 0; OneDrive = 0; SharePoint = 0; Closed = 0; Disconnected = 0; Backups = 0 }
function Get-Module { param([switch]$ListAvailable, $Name) @() }
function Connect-VBOServer { param($Server) }
function Disconnect-VBOServer { $global:counts.Disconnected++ }
function Get-VBOOrganization { param($Name) [pscustomobject]@{ Name=$Name; Id='org1' } }
function Get-VBOJob { param($Organization, $Name) [pscustomobject]@{ Name=$Name; Id='job1' } }
function Get-VBORestorePoint { param($Job, [switch]$Latest) [pscustomobject]@{ Id='point1'; BackupTime='2026-09-26' } }
function Start-VBOJob { param($Job) $global:counts.Backups++; [pscustomobject]@{ Id='new-session' } }
function Get-VBOJobSession { param($Job, [switch]$Last) [pscustomobject]@{ Id=$(if($global:counts.Backups){'new-session'}else{'old-session'}); Status='Success'; Progress=100 } }
function Start-VBOExchangeItemRestoreSession { param($RestorePoint) 'exchange-session' }
function Start-VEODRestoreSession { param($RestorePoint) 'onedrive-session' }
function Start-VBOSharePointItemRestoreSession { param($RestorePoint) 'sharepoint-session' }
function Stop-VBOExchangeItemRestoreSession { param($Session) $global:counts.Closed++ }
function Stop-VEODRestoreSession { param($Session) $global:counts.Closed++ }
function Stop-VBOSharePointItemRestoreSession { param($Session) $global:counts.Closed++ }
function Get-VEXDatabase { param($Session) 'database' }
function Get-VEXMailbox { param($Database) [pscustomobject]@{ Email='user@example.test'; Name='Mailbox'; IsDeleted=$false } }
function Get-VEXItem {
    param($Mailbox)
    for ($i=0; $i -lt 1000; $i++) {
        $global:counts.Exchange++
        [pscustomobject]@{ ItemClass='IPM.Note'; Subject="Sample $i"; Sent='2026-09-26' }
    }
}
function Get-VEODUser { param($Session) [pscustomobject]@{ Name='User' } }
function Get-VEODDocument {
    param($User, [switch]$Recurse)
    for ($i=0; $i -lt 1000; $i++) {
        $global:counts.OneDrive++
        [pscustomobject]@{ Name="sample$i.docx"; IsContainer=$false }
    }
}
function Get-VESPOrganization { param($Session) 'org' }
function Get-VESPSite { param($Organization, [switch]$Recurse) [pscustomobject]@{ Name='Site'; Url='https://example.test/site' } }
function Get-VESPDocumentLibrary { param($Site, [switch]$Recurse) [pscustomobject]@{ Name='Documents' } }
function Get-VESPDocument {
    param($DocumentLibrary, [switch]$Recurse)
    for ($i=0; $i -lt 1000; $i++) {
        $global:counts.SharePoint++
        [pscustomobject]@{ Name="sample$i.docx"; IsContainer=$false }
    }
}
function Export-VEXItem { param($Item, $To, [switch]$Force) [IO.File]::WriteAllBytes((Join-Path $To 'email.msg'), [byte[]](1,2,3)) }
function Save-VEODDocument { param($Document, $Path) [IO.File]::WriteAllBytes((Join-Path $Path $Document.Name), [byte[]](4,5,6)) }
function Save-VESPItem { param($Document, $Path, [switch]$Force) [IO.File]::WriteAllBytes((Join-Path $Path $Document.Name), [byte[]](7,8,9)) }
