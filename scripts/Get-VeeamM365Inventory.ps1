#requires -Version 7.0
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$connected = $false

try {
    if (-not (Get-Module -ListAvailable -Name 'Veeam.Archiver.PowerShell')) {
        throw "The Veeam.Archiver.PowerShell module is not installed or is not visible to PowerShell 7."
    }

    Import-Module Veeam.Archiver.PowerShell -ErrorAction Stop
    Connect-VBOServer -Server 'localhost' -ErrorAction Stop | Out-Null
    $connected = $true

    $organizations = @()
    foreach ($organization in @(Get-VBOOrganization | Sort-Object -Property Name)) {
        $jobs = @()
        try {
            $organizationJobs = @(Get-VBOJob -Organization $organization -ErrorAction Stop)
        }
        catch {
            $organizationJobs = @(Get-VBOJob | Where-Object {
                ($_.Organization -and $_.Organization.Name -eq $organization.Name) -or
                ($_.OrganizationId -and $_.OrganizationId -eq $organization.Id)
            })
        }

        foreach ($job in @($organizationJobs | Sort-Object -Property Name)) {
            $jobs += [ordered]@{
                Name = [string]$job.Name
                Id   = [string]$job.Id
            }
        }

        $organizations += [ordered]@{
            Name = [string]$organization.Name
            Id   = [string]$organization.Id
            Jobs = $jobs
        }
    }

    $payload = [ordered]@{
        Server        = 'localhost'
        Organizations = $organizations
    } | ConvertTo-Json -Depth 6 -Compress

    Write-Output "__VEEAM_INVENTORY__$payload"
}
catch {
    Write-Error $_.Exception.Message
    exit 2
}
finally {
    if ($connected) {
        Disconnect-VBOServer -ErrorAction SilentlyContinue
    }
}
