param(
    [switch] $WithTools,
    [switch] $ShellAliases
)

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:PYTHONPATH = "$Root;$env:PYTHONPATH"

python -m pip install -e "$Root" --no-deps
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

if ($WithTools) {
    if ($ShellAliases) {
        python -m opai --project "$Root" install --with-tools --shell-aliases
    } else {
        python -m opai --project "$Root" install --with-tools
    }
} else {
    if ($ShellAliases) {
        python -m opai --project "$Root" install --no-tools --shell-aliases
    } else {
        python -m opai --project "$Root" install --no-tools
    }
}

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "OPai 0.1.0 pre-alpha installed."
Write-Host "Next: op activate --repair --shell-aliases"
Write-Host "Status: op status"
