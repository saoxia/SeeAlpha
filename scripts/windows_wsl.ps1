# SeeAlpha Windows launcher helpers. WSL2 only; no native Windows Engine.
Set-StrictMode -Version Latest

function ConvertFrom-SeeAlphaWSLList {
    param([object[]]$Lines)
    foreach ($entry in $Lines) {
        $line = ([string]$entry).Replace([string][char]0, '').Trim()
        # State is localized; the last column is always WSL version 1 or 2.
        $match = [regex]::Match($line, '^\*?\s*(?<Name>[^\s]+)\s+.+\s+(?<Version>[12])\s*$')
        if ($match.Success) {
            [pscustomobject]@{
                Name = $match.Groups['Name'].Value
                Version = [int]$match.Groups['Version'].Value
            }
        }
    }
}

function Select-SeeAlphaWSLDistribution {
    param(
        [object[]]$Distributions,
        [string]$Requested = 'Ubuntu',
        [bool]$Explicit = $false
    )
    if ($Requested -cnotmatch '^Ubuntu(?:[-.][A-Za-z0-9._-]+)?$') {
        throw '当前仅对 Ubuntu WSL2 发行版提供安装支持，请指定 Ubuntu 或 Ubuntu-24.04 等名称。'
    }
    $exact = @($Distributions | Where-Object { $_.Name -eq $Requested })
    if ($exact.Count -gt 0) {
        if ($exact[0].Version -ne 2) {
            throw "发行版 $Requested 当前为 WSL1；本工具不会自动转换或修改已有发行版。请另行备份并手动升级至 WSL2。"
        }
        return $exact[0].Name
    }
    if (-not $Explicit -and $Requested -eq 'Ubuntu') {
        $variants = @($Distributions | Where-Object {
            $_.Name -match '^Ubuntu[-.]' -and $_.Version -eq 2
        })
        if ($variants.Count -eq 1) {
            return $variants[0].Name
        }
        if ($variants.Count -gt 1) {
            throw '检测到多个 Ubuntu WSL2 发行版，请用 -Distro 明确选择一个。'
        }
        $old = @($Distributions | Where-Object { $_.Name -match '^Ubuntu(?:[-.]|$)' -and $_.Version -eq 1 })
        if ($old.Count -gt 0) {
            throw '已有 Ubuntu WSL1，未找到 Ubuntu WSL2。请手动升级或用 -Distro 选择一个独立的 Ubuntu WSL2 发行版。'
        }
    }
    return $null
}

function New-SeeAlphaLinuxCommand {
    param(
        [ValidateSet('install', 'start', 'stop', 'status', 'doctor', 'admin')]
        [string]$Command,
        [string]$Action = '',
        [string]$Username = 'admin'
    )
    $prefix = @'
set -eu
if [ "$(id -u)" -eq 0 ]; then
  echo "请先从 Ubuntu 应用完成首次启动并创建普通 Linux 用户；不允许用 root 安装 SeeAlpha。" >&2
  exit 2
fi
project="$HOME/seealpha"
if [ -L "$project" ]; then
  echo "发现符号链接 ~/seealpha，拒绝自动接管。" >&2
  exit 2
fi
'@
    if ($Command -eq 'install') {
        return $prefix + @'

if ! command -v git >/dev/null 2>&1; then
  echo "WSL Ubuntu 缺少 Git；请在 Ubuntu 内执行 sudo apt-get update && sudo apt-get install -y git。" >&2
  exit 2
fi
if [ ! -e "$project" ]; then
  git clone --branch main --single-branch https://github.com/saoxia/SeeAlpha.git "$project"
fi
if [ ! -d "$project/.git" ] || [ ! -f "$project/seealpha" ]; then
  echo "目录 ~/seealpha 已存在但不是预期的项目，拒绝覆盖。" >&2
  exit 2
fi
remote="$(git -C "$project" remote get-url origin 2>/dev/null || true)"
case "$remote" in
  https://github.com/saoxia/SeeAlpha.git|git@github.com:saoxia/SeeAlpha.git) ;;
  *) echo "目录 ~/seealpha 的 Git 远端并非 SeeAlpha，拒绝接管。" >&2; exit 2 ;;
esac
cd "$project"
./seealpha install
'@
    }
    if ($Command -eq 'admin') {
        if ($Action -notin @('create', 'reset')) {
            throw 'admin 只接受 create 或 reset。'
        }
        if ($Username -cnotmatch '^[A-Za-z0-9_.-]{1,64}$') {
            throw '管理员名称必须是 1–64 位字母、数字、下划线、点或短横线。'
        }
        $linuxArgs = "admin $Action $Username"
    } else {
        $linuxArgs = $Command
    }
    $tail = @'

if [ ! -d "$project/.git" ] || [ ! -f "$project/seealpha" ]; then
  echo "WSL Ubuntu 尚未安装 SeeAlpha；请先运行 Windows 入口的 install 命令。" >&2
  exit 2
fi
cd "$project"
'@
    return $prefix + $tail + [Environment]::NewLine + "./seealpha $linuxArgs"
}

function Invoke-SeeAlphaLinux {
    param([string]$WslExe, [string]$Distro, [string]$Script)
    $payload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Script))
    # Pass one ASCII-only argument to WSL; never interpolate paths/user text into shell quoting.
    $shell = "printf '%s' '$payload' | base64 -d | bash"
    & $WslExe --distribution $Distro --exec bash -lc $shell
    if ($LASTEXITCODE -ne 0) {
        throw "WSL 内的 SeeAlpha 命令未完成（退出码：$LASTEXITCODE）。"
    }
}

function Invoke-SeeAlphaWindows {
    param(
        [string]$Command = 'help',
        [string]$Action = '',
        [string]$Username = 'admin',
        [string]$Distro = 'Ubuntu',
        [bool]$ExplicitDistro = $false,
        [switch]$Yes
    )
    if ($Command -eq 'help') {
        Write-Host 'SeeAlpha Windows / WSL2 本机入口：'
        Write-Host '  .\seealpha.ps1 install [-Distro Ubuntu-24.04] [-Yes]'
        Write-Host '  .\seealpha.ps1 start|stop|status|doctor [-Distro Ubuntu-24.04]'
        Write-Host '  .\seealpha.ps1 admin create|reset [用户名] [-Distro Ubuntu-24.04]'
        Write-Host 'Windows 仅作引导；Engine、Platform 与 Web 在 WSL2 Ubuntu 内运行。'
        return
    }
    if ($Command -notin @('install', 'start', 'stop', 'status', 'doctor', 'admin')) {
        throw '未知命令；执行 .\seealpha.ps1 help 查看帮助。'
    }
    # Validate arguments before invoking any external program or elevated action.
    $null = New-SeeAlphaLinuxCommand -Command $Command -Action $Action -Username $Username
    if ($Distro -cnotmatch '^Ubuntu(?:[-.][A-Za-z0-9._-]+)?$') {
        throw '只能选择 Ubuntu WSL2 发行版。'
    }
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'Windows 入口只能在 Windows 10/11 上运行。macOS/Linux 请直接使用 ./seealpha。'
    }
    $wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
    if ($null -eq $wsl) {
        throw '找不到 wsl.exe；请先确认 Windows 版本与 WSL 功能支持情况。'
    }
    $wslExe = $wsl.Source
    $all = @(& $wslExe --list --verbose 2>&1)
    $distributions = @(ConvertFrom-SeeAlphaWSLList -Lines $all)
    $selected = Select-SeeAlphaWSLDistribution -Distributions $distributions -Requested $Distro -Explicit:$ExplicitDistro
    if ($null -eq $selected) {
        if ($Command -ne 'install') {
            throw '未找到所选 Ubuntu WSL2 发行版。请先执行 .\seealpha.ps1 install。'
        }
        if (-not $Yes) {
            $answer = Read-Host "未找到 Ubuntu WSL2 $Distro。允许以管理员权限运行 wsl --install --distribution $Distro 吗？[y/N]"
            if ($answer -notin @('y', 'Y', 'yes', 'YES')) {
                Write-Host '已取消。没有修改 WSL 设置。'
                return
            }
        }
        Write-Host "即将运行 Windows 官方 WSL 安装命令（Windows 可能弹出 UAC）：wsl --install --distribution $Distro"
        $process = Start-Process -FilePath $wslExe -ArgumentList @('--install', '--distribution', $Distro) -Wait -PassThru -Verb RunAs
        if ($process.ExitCode -ne 0) {
            throw "WSL 安装退出码：$($process.ExitCode)。请查阅微软 WSL 故障排除文档。"
        }
        Write-Host 'WSL 安装命令已返回。可能需要重启 Windows；请首次启动 Ubuntu 并创建普通 Linux 用户，然后重新执行本命令。'
        return
    }
    Write-Host "使用已安装的 WSL2 发行版：$selected（停止状态会在运行 Linux 命令时自动启动）。"
    $script = New-SeeAlphaLinuxCommand -Command $Command -Action $Action -Username $Username
    Invoke-SeeAlphaLinux -WslExe $wslExe -Distro $selected -Script $script
    if ($Command -eq 'start') {
        Write-Host '可在 Windows 浏览器打开 http://127.0.0.1:3000（需 WSL localhost 转发正常）。'
    }
}
