# Vesta Install Layer

Vesta 0.2.0 alpha.1 installs as a local Python CLI. The default bootstrap does not call paid APIs, enable cloud tools, or install optional scanners.

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
python -m pip install -e .
vesta install --no-tools
```

Optional free tool install:

```sh
vesta install --with-tools
```
