param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $Args
)

$Root = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = "$Root;$env:PYTHONPATH"
python -m opaihub @Args
