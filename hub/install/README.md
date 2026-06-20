# OPai Install Layer

OPai 0.2.0 alpha.1 installs as a local Python CLI. The default bootstrap does not call paid APIs, enable cloud tools, or install optional scanners.

## Local Install

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

macOS/Linux:

```sh
sh ./install.sh
```

Developer install:

```sh
python -m pip install -e . --no-deps
opai install --no-tools
```

Optional free tool install:

```sh
opai install --with-tools
```
