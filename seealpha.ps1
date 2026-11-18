# Windows entry point: SeeAlpha runs only inside a WSL2 Ubuntu distribution.
[CmdletBinding()]
param(
    [Parameter(Position = 0)][string]$Command = 'help',
    [Parameter(Position = 1)][string]$Action = '',
    [Parameter(Position = 2)][string]$Username = 'admin',
    [string]$Distro = 'Ubuntu',
    [switch]$Yes
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts/windows_wsl.ps1')
try {
    $explicitDistro = $PSBoundParameters.ContainsKey('Distro')
    Invoke-SeeAlphaWindows -Command $Command -Action $Action -Username $Username -Distro $Distro -ExplicitDistro $explicitDistro -Yes:$Yes
} catch {
    [Console]::Error.WriteLine("SeeAlpha Windows: $($_.Exception.Message)")
    exit 1
}
