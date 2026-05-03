# Commands

## Onboard

```powershell
python -m opcoding init C:\repo
```

Creates:

- `.opcoding/project.json`
- `.opcoding/context.md`
- `.opcoding/budget.json`
- `.opcoding/enabled-agents.json`
- `.opcoding/mcp.profile.json`

## Route A Task

```powershell
python -m opcoding route "fix failing checkout tests" --project C:\repo
python -m opcoding plan "add password reset" --project C:\repo
python -m opcoding refactor src/auth "split token validation" --project C:\repo
```

## Get Local Evidence

```powershell
python -m opcoding git C:\repo summary
python -m opcoding test C:\repo --dry-run
python -m opcoding review C:\repo
```

## Diagnose A Failure

```powershell
python -m opcoding fix C:\repo --cmd "npm test -- checkout"
```

## Final-Layer Commands

```powershell
python -m opcoding agents "build and ship a notes app" --project C:\repo
python -m opcoding ask "debug flaky checkout test" --project C:\repo
python -m opcoding index C:\repo build
python -m opcoding index C:\repo search CheckoutService
python -m opcoding memory C:\repo decision "Use Vitest" "Matches existing Vite stack"
python -m opcoding mcp C:\repo doctor
python -m opcoding ship "create dashboard app" --project C:\repo
python -m opcoding deploy C:\repo
python -m opcoding dashboard C:\repo
python -m opcoding hooks C:\repo install
python -m opcoding ci C:\repo github
python -m opcoding tools C:\repo install --set core
python -m opcoding tools C:\repo run ruff
python -m opcoding morph C:\repo doctor
```
