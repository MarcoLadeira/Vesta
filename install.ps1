param(
    [switch] $WithTools,
    [switch] $NoShellAliases,
    [switch] $NoSuperpowers,
    [string] $ProjectRoot,
    [string] $InstallRoot
)

$ErrorActionPreference = "Stop"

$RepoUrl = if ($env:OPAI_REPO_URL) { $env:OPAI_REPO_URL } else { "https://github.com/MarcoLadeira/OPai.git" }
$Branch = if ($env:OPAI_BRANCH) { $env:OPAI_BRANCH } else { "main" }
$InstallTools = $WithTools -or $env:OPAI_WITH_TOOLS -eq "1"
$SkipSuperpowers = $NoSuperpowers -or $env:OPAI_NO_SUPERPOWERS -eq "1"

if (-not $ProjectRoot) {
    $ProjectRoot = if ($env:OPAI_PROJECT_ROOT) {
        $env:OPAI_PROJECT_ROOT
    } else {
        (Get-Location).Path
    }
}

if (-not $InstallRoot) {
    $InstallRoot = if ($env:OPAI_INSTALL_ROOT) {
        $env:OPAI_INSTALL_ROOT
    } else {
        Join-Path $env:USERPROFILE ".opai\source"
    }
}

function Get-LocalSourceRoot {
    $ScriptPath = $PSCommandPath
    if (-not $ScriptPath) {
        $ScriptPath = $MyInvocation.ScriptName
    }
    if (-not $ScriptPath) {
        $ScriptPath = $MyInvocation.MyCommand.Path
    }
    if ($ScriptPath) {
        $Candidate = Split-Path -Parent $ScriptPath
        if ($Candidate -and (Test-Path (Join-Path $Candidate "pyproject.toml"))) {
            return $Candidate
        }
    }
    return $null
}

function Ensure-RemoteSource {
    param(
        [string] $Target,
        [string] $Url,
        [string] $Ref
    )

    if (-not (Get-Command git -CommandType Application -ErrorAction SilentlyContinue)) {
        Write-Error "Git is required for the one-command OPai installer. Install Git, then re-run this command."
        exit 127
    }

    $Parent = Split-Path -Parent $Target
    if ($Parent) {
        New-Item -ItemType Directory -Force -Path $Parent | Out-Null
    }

    if (Test-Path (Join-Path $Target ".git")) {
        git -C $Target fetch origin $Ref
        git -C $Target checkout $Ref
        git -C $Target pull --ff-only origin $Ref
    } elseif (Test-Path $Target) {
        if (-not (Test-Path (Join-Path $Target "pyproject.toml"))) {
            Write-Error "Install target exists but is not an OPai checkout: $Target"
            exit 1
        }
    } else {
        git clone --depth 1 --branch $Ref $Url $Target
    }
    return $Target
}

$Root = Get-LocalSourceRoot
if (-not $Root) {
    $Root = Ensure-RemoteSource -Target $InstallRoot -Url $RepoUrl -Ref $Branch
}

$Root = (Resolve-Path $Root).Path
$ProjectRoot = (Resolve-Path $ProjectRoot).Path
$env:PYTHONPATH = "$Root;$env:PYTHONPATH"

$Python = if ($env:OPAI_PYTHON) {
    $env:OPAI_PYTHON
} else {
    (Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
}

& $Python -m pip install -e "$Root"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$InstallArgs = @("install", "--project", $ProjectRoot)
if ($InstallTools) {
    $InstallArgs += "--with-tools"
} else {
    $InstallArgs += "--no-tools"
}
if ($SkipSuperpowers) {
    $InstallArgs += "--no-superpowers"
}
if (-not $NoShellAliases) {
    $InstallArgs += "--shell-aliases"
}

& $Python -m opai @InstallArgs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$InstalledVersion = (& $Python -m opai version 2>$null)
if (-not $InstalledVersion) { $InstalledVersion = "OPai installed" }
Write-Host ""
Write-Host "$InstalledVersion installed permanently."
Write-Host "Source: $Root"
Write-Host "Activated project: $ProjectRoot"
Write-Host "Restart terminals and AI clients once so aliases and skills reload."
Write-Host "Use in any repo: op status"
Write-Host "Launch with OPai: op launch codex"
