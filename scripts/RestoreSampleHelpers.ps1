function Write-SampleExportWarning {
    param([string]$Message)

    if (Get-Command -Name Write-WarnLog -CommandType Function -ErrorAction SilentlyContinue) {
        Write-WarnLog $Message
    } else {
        Write-Warning $Message
    }
}

function Test-RestoreDocumentCandidate {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory, ValueFromPipeline)]
        [AllowNull()]
        [object]$Item,

        [switch]$RequireExtension
    )

    process {
        if ($null -eq $Item) {
            return $false
        }

        $name = [string]$Item.Name
        if ([string]::IsNullOrWhiteSpace($name)) {
            return $false
        }

        foreach ($propertyName in @('IsContainer', 'IsFolder')) {
            $property = $Item.PSObject.Properties[$propertyName]
            if ($null -ne $property -and [bool]$property.Value) {
                return $false
            }
        }

        if ($RequireExtension -and -not [IO.Path]::HasExtension($name)) {
            return $false
        }

        return $true
    }
}

function Invoke-VerifiedSampleExport {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [object[]]$Candidates,

        [Parameter(Mandatory)]
        [string]$DestinationRoot,

        [Parameter(Mandatory)]
        [scriptblock]$ExportAction,

        [Parameter(Mandatory)]
        [string]$Workload,

        [ValidateRange(1, 1000)]
        [int]$MaxAttempts = 25,

        [switch]$DisableRandomization
    )

    $candidateList = @($Candidates | Where-Object { $null -ne $_ })
    if ($candidateList.Count -eq 0) {
        return [pscustomobject]@{
            Success   = $false
            Candidate = $null
            File      = $null
            SHA256    = $null
            Attempts  = 0
            LastError = "Nessun candidato disponibile per $Workload."
        }
    }

    New-Item -ItemType Directory -Path $DestinationRoot -Force | Out-Null
    $orderedCandidates = if ($DisableRandomization) {
        $candidateList
    } else {
        @($candidateList | Get-Random -Count $candidateList.Count)
    }

    $workspace = Join-Path $DestinationRoot ('.sample-attempts-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $workspace -Force | Out-Null
    $attempts = 0
    $lastError = "Nessun file non vuoto esportato per $Workload."

    try {
        foreach ($candidate in $orderedCandidates) {
            if ($attempts -ge $MaxAttempts) {
                break
            }

            $attempts++
            $attemptDirectory = Join-Path $workspace ('attempt-{0:D3}' -f $attempts)
            New-Item -ItemType Directory -Path $attemptDirectory -Force | Out-Null
            $destinationPath = $null

            try {
                & $ExportAction $candidate $attemptDirectory

                $validFiles = @(
                    Get-ChildItem -LiteralPath $attemptDirectory -File -Recurse -ErrorAction Stop |
                        Where-Object { $_.Length -gt 0 } |
                        Sort-Object -Property Length -Descending
                )
                if ($validFiles.Count -eq 0) {
                    $lastError = "Il candidato $attempts per $Workload non ha prodotto file con dimensione maggiore di 0 byte."
                    Write-SampleExportWarning "$lastError Provo un altro elemento."
                    continue
                }

                $sourceFile = $validFiles[0]
                $destinationPath = Join-Path $DestinationRoot $sourceFile.Name
                if (Test-Path -LiteralPath $destinationPath) {
                    $stem = [IO.Path]::GetFileNameWithoutExtension($sourceFile.Name)
                    $extension = [IO.Path]::GetExtension($sourceFile.Name)
                    $destinationPath = Join-Path $DestinationRoot ("{0}_{1}{2}" -f $stem, [guid]::NewGuid().ToString('N').Substring(0, 8), $extension)
                }

                Copy-Item -LiteralPath $sourceFile.FullName -Destination $destinationPath -Force -ErrorAction Stop
                $finalFile = Get-Item -LiteralPath $destinationPath -ErrorAction Stop
                if ($finalFile.Length -le 0) {
                    throw "Il file copiato per $Workload ha dimensione pari a 0 byte."
                }

                $hash = (Get-FileHash -LiteralPath $finalFile.FullName -Algorithm SHA256 -ErrorAction Stop).Hash
                return [pscustomobject]@{
                    Success   = $true
                    Candidate = $candidate
                    File      = $finalFile
                    SHA256    = $hash
                    Attempts  = $attempts
                    LastError = $null
                }
            } catch {
                $lastError = $_.Exception.Message
                if ($null -ne $destinationPath -and (Test-Path -LiteralPath $destinationPath)) {
                    Remove-Item -LiteralPath $destinationPath -Force -ErrorAction SilentlyContinue
                }
                Write-SampleExportWarning "Candidato $attempts per $Workload non utilizzabile: $lastError. Provo un altro elemento."
            } finally {
                if (Test-Path -LiteralPath $attemptDirectory) {
                    Remove-Item -LiteralPath $attemptDirectory -Recurse -Force -ErrorAction SilentlyContinue
                }
            }
        }
    } finally {
        if (Test-Path -LiteralPath $workspace) {
            Remove-Item -LiteralPath $workspace -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    return [pscustomobject]@{
        Success   = $false
        Candidate = $null
        File      = $null
        SHA256    = $null
        Attempts  = $attempts
        LastError = $lastError
    }
}
