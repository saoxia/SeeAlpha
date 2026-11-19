$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot '../scripts/windows_wsl.ps1')

function Assert-SeeAlpha([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

$installed = @(ConvertFrom-SeeAlphaWSLList -Lines @(
    '  NAME                 STATE           VERSION',
    '* Ubuntu-24.04        Stopped         2',
    '  Debian               Running         1'
))
Assert-SeeAlpha ($installed.Count -eq 2) 'WSL output parse count'
Assert-SeeAlpha ($installed[0].Name -eq 'Ubuntu-24.04') 'WSL distro name'
Assert-SeeAlpha ($installed[1].Version -eq 1) 'WSL version parse'
Assert-SeeAlpha (
    (Select-SeeAlphaWSLDistribution -Distributions $installed -Requested 'Ubuntu') -eq 'Ubuntu-24.04'
) 'Reuses unique installed Ubuntu WSL2'
Assert-SeeAlpha (
    $null -eq (Select-SeeAlphaWSLDistribution -Distributions @() -Requested 'Ubuntu')
) 'No distro requires WSL installation'

$localized = @(ConvertFrom-SeeAlphaWSLList -Lines @(
    '  NAME                 状态               版本',
    '* Ubuntu              已停止             2'
))
Assert-SeeAlpha ($localized.Count -eq 1 -and $localized[0].Version -eq 2) 'Localized WSL state'

$refusedWsl1 = $false
try {
    $null = Select-SeeAlphaWSLDistribution -Distributions @(
        [pscustomobject]@{ Name = 'Ubuntu'; Version = 1 }
    ) -Requested 'Ubuntu'
} catch {
    $refusedWsl1 = $_.Exception.Message -match 'WSL1'
}
Assert-SeeAlpha $refusedWsl1 'Never silently converts WSL1'

$refusedAmbiguous = $false
try {
    $null = Select-SeeAlphaWSLDistribution -Distributions @(
        [pscustomobject]@{ Name = 'Ubuntu-22.04'; Version = 2 },
        [pscustomobject]@{ Name = 'Ubuntu-24.04'; Version = 2 }
    ) -Requested 'Ubuntu'
} catch {
    $refusedAmbiguous = $_.Exception.Message -match '多个'
}
Assert-SeeAlpha $refusedAmbiguous 'Ambiguous Ubuntu versions require explicit distro'

$install = New-SeeAlphaLinuxCommand -Command install
Assert-SeeAlpha ($install.Contains('git clone --branch main --single-branch')) 'Linux clone stays on ext4 home'
Assert-SeeAlpha ($install.Contains('./seealpha install')) 'Linux installer reused'
Assert-SeeAlpha ($install.Contains('remote get-url origin')) 'Existing repo origin verified'
Assert-SeeAlpha ($install.Contains('id -u')) 'Disallow root install'
$up = New-SeeAlphaLinuxCommand -Command up -Yes $true
Assert-SeeAlpha ($up.Contains('./seealpha up')) 'Windows up delegates to Linux up'
Assert-SeeAlpha ($up.Contains('sudo apt-get install -y git curl')) 'Missing Ubuntu tools are bootstrapped'
Assert-SeeAlpha ($up.Contains('consent="yes"')) 'Unattended consent is explicit'
Assert-SeeAlpha ($up.Contains('remote get-url origin')) 'Windows up verifies existing checkout'
$admin = New-SeeAlphaLinuxCommand -Command admin -Action create -Username user_1
Assert-SeeAlpha ($admin.Contains('./seealpha admin create user_1')) 'Admin delegates to Linux'
$status = New-SeeAlphaLinuxCommand -Command status
Assert-SeeAlpha ($status.Contains('./seealpha status')) 'Status delegates to Linux'

$badUsername = $false
try {
    $null = New-SeeAlphaLinuxCommand -Command admin -Action create -Username 'bad;cmd'
} catch {
    $badUsername = $_.Exception.Message -match '管理员名称'
}
Assert-SeeAlpha $badUsername 'Reject shell metacharacters in admin username'

Write-Host 'Windows WSL2 launch plan static tests passed (no WSL install or system modifications).'
