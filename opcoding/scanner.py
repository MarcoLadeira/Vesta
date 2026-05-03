from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .utils import iter_project_files, load_json, now_iso, run_command, safe_rel


EXT_LANG = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".cs": "csharp",
    ".fs": "fsharp",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".php": "php",
    ".rb": "ruby",
    ".swift": "swift",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".sql": "sql",
    ".sh": "shell",
    ".ps1": "powershell",
}

DOC_NAMES = {
    "readme.md",
    "readme",
    "contributing.md",
    "architecture.md",
    "security.md",
    "changelog.md",
    "license",
}


def _package_json(root: Path) -> dict[str, Any]:
    return load_json(root / "package.json", {})


def detect_package_managers(root: Path) -> list[str]:
    managers: list[str] = []
    if (root / "pnpm-lock.yaml").exists():
        managers.append("pnpm")
    if (root / "yarn.lock").exists():
        managers.append("yarn")
    if (root / "package-lock.json").exists():
        managers.append("npm")
    if (root / "bun.lockb").exists() or (root / "bun.lock").exists():
        managers.append("bun")
    if (root / "pyproject.toml").exists():
        managers.append("python")
    if (root / "requirements.txt").exists():
        managers.append("pip")
    if (root / "poetry.lock").exists():
        managers.append("poetry")
    if (root / "Pipfile").exists():
        managers.append("pipenv")
    if (root / "go.mod").exists():
        managers.append("go")
    if (root / "Cargo.toml").exists():
        managers.append("cargo")
    if any(root.glob("*.csproj")) or any(root.glob("*.sln")):
        managers.append("dotnet")
    if (root / "pom.xml").exists():
        managers.append("maven")
    if (root / "build.gradle").exists() or (root / "build.gradle.kts").exists():
        managers.append("gradle")
    return sorted(set(managers))


def detect_frameworks(root: Path) -> list[str]:
    frameworks: set[str] = set()
    package = _package_json(root)
    deps = {}
    for key in ["dependencies", "devDependencies", "peerDependencies"]:
        deps.update(package.get(key, {}) if isinstance(package.get(key), dict) else {})
    if "next" in deps:
        frameworks.add("nextjs")
    if "react" in deps:
        frameworks.add("react")
    if "vue" in deps:
        frameworks.add("vue")
    if "svelte" in deps or "@sveltejs/kit" in deps:
        frameworks.add("svelte")
    if "vite" in deps:
        frameworks.add("vite")
    if "express" in deps:
        frameworks.add("express")
    if "nestjs" in " ".join(deps.keys()) or "@nestjs/core" in deps:
        frameworks.add("nestjs")
    if "jest" in deps:
        frameworks.add("jest")
    if "vitest" in deps:
        frameworks.add("vitest")
    if "playwright" in deps or "@playwright/test" in deps:
        frameworks.add("playwright")
    pyproject_text = (
        (root / "pyproject.toml").read_text(encoding="utf-8", errors="ignore")
        if (root / "pyproject.toml").exists()
        else ""
    )
    if "pytest" in pyproject_text or (root / "pytest.ini").exists():
        frameworks.add("pytest")
    if (root / "manage.py").exists():
        frameworks.add("django")
    if any(path.name == "main.py" for path in root.glob("**/main.py")) and any(
        "fastapi" in path.name.lower() for path in root.glob("*")
    ):
        frameworks.add("fastapi")
    if any(root.glob("*.csproj")):
        csproj_text = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")[:4000]
            for path in root.glob("*.csproj")
        )
        if "Microsoft.NET.Sdk.Web" in csproj_text:
            frameworks.add("aspnet-core")
        if "xunit" in csproj_text.lower():
            frameworks.add("xunit")
    if (root / "Dockerfile").exists():
        frameworks.add("docker")
    return sorted(frameworks)


def detect_commands(root: Path, managers: list[str]) -> dict[str, str]:
    commands: dict[str, str] = {}
    package = _package_json(root)
    scripts = (
        package.get("scripts", {}) if isinstance(package.get("scripts"), dict) else {}
    )
    runner = "npm"
    if "pnpm" in managers:
        runner = "pnpm"
    elif "yarn" in managers:
        runner = "yarn"
    elif "bun" in managers:
        runner = "bun"
    if package:
        commands["install"] = f"{runner} install"
        for name in ["build", "test", "lint", "typecheck", "format", "dev"]:
            if name in scripts:
                commands[name] = (
                    f"{runner} run {name}" if runner != "npm" else f"npm run {name}"
                )
        if "test" in scripts and runner == "npm":
            commands["test"] = "npm test"
    pyproject_text = (
        (root / "pyproject.toml").read_text(encoding="utf-8", errors="ignore")
        if (root / "pyproject.toml").exists()
        else ""
    )
    if "pytest" in pyproject_text or (root / "pytest.ini").exists():
        commands.setdefault("test", "pytest")
        commands.setdefault("targeted_test", "pytest {files}")
    elif (root / "tests").exists():
        commands.setdefault("test", "python -m unittest discover -s tests")
        commands.setdefault("targeted_test", "python -m unittest {files}")
    if (root / "requirements.txt").exists():
        commands.setdefault("install", "python -m pip install -r requirements.txt")
    if any(root.glob("*.sln")) or any(root.glob("*.csproj")):
        commands.setdefault("build", "dotnet build")
        commands.setdefault("test", "dotnet test")
    if (root / "go.mod").exists():
        commands.setdefault("build", "go build ./...")
        commands.setdefault("test", "go test ./...")
    if (root / "Cargo.toml").exists():
        commands.setdefault("build", "cargo build")
        commands.setdefault("test", "cargo test")
    if (root / "pom.xml").exists():
        commands.setdefault("build", "mvn package")
        commands.setdefault("test", "mvn test")
    if (root / "build.gradle").exists() or (root / "build.gradle.kts").exists():
        commands.setdefault("build", "gradle build")
        commands.setdefault("test", "gradle test")
    return commands


def detect_docs(root: Path, files: list[Path]) -> list[str]:
    docs: list[str] = []
    for path in files:
        rel = safe_rel(path, root)
        if path.name.lower() in DOC_NAMES or rel.lower().startswith("docs/"):
            docs.append(rel)
    return sorted(docs)[:80]


def detect_ci(root: Path) -> list[str]:
    paths: list[str] = []
    for pattern in [
        ".github/workflows/*.yml",
        ".github/workflows/*.yaml",
        ".gitlab-ci.yml",
        "azure-pipelines.yml",
        "Jenkinsfile",
        "Dockerfile",
        "docker-compose.yml",
        "compose.yml",
    ]:
        for path in root.glob(pattern):
            paths.append(safe_rel(path, root))
    return sorted(set(paths))


def detect_git(root: Path) -> dict[str, Any]:
    status = run_command("git status --short --branch -- .", root, timeout=20)
    branch = run_command("git branch --show-current", root, timeout=20)
    remote = run_command("git remote -v", root, timeout=20)
    return {
        "is_repo": status.returncode == 0,
        "branch": branch.stdout.strip(),
        "status": status.stdout.strip(),
        "remotes": remote.stdout.strip().splitlines()[:10]
        if remote.returncode == 0
        else [],
    }


def top_level_dirs(root: Path, files: list[Path]) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for path in files:
        rel = Path(safe_rel(path, root))
        first = rel.parts[0] if len(rel.parts) > 1 else "."
        counts[first] += 1
    return [{"path": name, "files": count} for name, count in counts.most_common(20)]


def scan_project(root: Path) -> dict[str, Any]:
    root = root.resolve()
    files = list(iter_project_files(root))
    language_counts: Counter[str] = Counter()
    extension_counts: Counter[str] = Counter()
    manifests: list[str] = []
    by_dir: defaultdict[str, int] = defaultdict(int)

    manifest_names = {
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "go.mod",
        "Cargo.toml",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "Dockerfile",
        "compose.yml",
        "docker-compose.yml",
    }

    for path in files:
        ext = path.suffix.lower()
        if ext:
            extension_counts[ext] += 1
        lang = EXT_LANG.get(ext)
        if lang:
            language_counts[lang] += 1
        if path.name in manifest_names:
            manifests.append(safe_rel(path, root))
        rel = Path(safe_rel(path, root))
        by_dir[rel.parts[0] if len(rel.parts) > 1 else "."] += 1

    managers = detect_package_managers(root)
    profile = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "name": root.name,
        "root": str(root),
        "file_count": len(files),
        "languages": [name for name, _ in language_counts.most_common()],
        "language_counts": dict(language_counts.most_common()),
        "extension_counts": dict(extension_counts.most_common(25)),
        "frameworks": detect_frameworks(root),
        "package_managers": managers,
        "manifests": sorted(manifests),
        "commands": detect_commands(root, managers),
        "docs": detect_docs(root, files),
        "ci": detect_ci(root),
        "git": detect_git(root),
        "top_level": top_level_dirs(root, files),
    }
    return profile
