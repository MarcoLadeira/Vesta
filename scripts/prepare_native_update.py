"""Prepare signed-package layouts and embedded updater runtime identity."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from opai.update.models import InstallType  # noqa: E402
from opai.asset_identity import asset_manifest  # noqa: E402
from opai.update.packaging import (  # noqa: E402
    make_msix,
    prepare_macos_sparkle_bundle,
    prepare_msix_layout,
    runtime_identity,
    write_runtime_configuration,
)
from opai.update.release import ReleaseError  # noqa: E402


def _object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReleaseError("JSON input must be an object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    configure = commands.add_parser("configure")
    configure.add_argument("--bundle", type=Path, required=True)
    configure.add_argument("--trust", type=Path, required=True)
    configure.add_argument("--asset-root", type=Path, required=True)
    for command in (configure,):
        command.add_argument("--version", required=True)
        command.add_argument("--build-id", required=True)
        command.add_argument("--channel", required=True)
        command.add_argument("--platform", required=True)
        command.add_argument("--architecture", required=True)
        command.add_argument(
            "--install-type",
            choices=[item.value for item in InstallType],
            required=True,
        )
        command.add_argument("--package-identity", required=True)
        command.add_argument("--publisher-identity", required=True)

    windows = commands.add_parser("windows-layout")
    windows.add_argument("--bundle", type=Path, required=True)
    windows.add_argument("--layout", type=Path, required=True)
    windows.add_argument("--assets", type=Path, required=True)
    windows.add_argument("--version", required=True)
    windows.add_argument("--architecture", required=True)
    windows.add_argument("--package-identity", required=True)
    windows.add_argument("--publisher-identity", required=True)

    pack = commands.add_parser("make-msix")
    pack.add_argument("--layout", type=Path, required=True)
    pack.add_argument("--output", type=Path, required=True)
    pack.add_argument("--makeappx", type=Path, required=True)

    mac = commands.add_parser("macos-sparkle")
    mac.add_argument("--bundle", type=Path, required=True)
    mac.add_argument("--sparkle-app", type=Path, required=True)
    mac.add_argument("--version", required=True)
    mac.add_argument("--feed-url", required=True)
    mac.add_argument("--sparkle-public-key", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "configure":
            identity = runtime_identity(
                version=args.version,
                build_id=args.build_id,
                channel=args.channel,
                platform=args.platform,
                architecture=args.architecture,
                install_type=InstallType(args.install_type),
                package_identity=args.package_identity,
                publisher_identity=args.publisher_identity,
                assets=asset_manifest(args.asset_root),
            )
            write_runtime_configuration(
                args.bundle, identity=identity, trust=_object(args.trust)
            )
        elif args.command == "windows-layout":
            prepare_msix_layout(
                args.bundle,
                args.layout,
                package_identity=args.package_identity,
                publisher_identity=args.publisher_identity,
                version=args.version,
                architecture=args.architecture,
                assets=args.assets,
            )
        elif args.command == "make-msix":
            make_msix(args.layout, args.output, makeappx=args.makeappx)
        elif args.command == "macos-sparkle":
            prepare_macos_sparkle_bundle(
                args.bundle,
                sparkle_app=args.sparkle_app,
                version=args.version,
                feed_url=args.feed_url,
                sparkle_public_key=args.sparkle_public_key,
            )
        print(json.dumps({"ok": True, "command": args.command}))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, ReleaseError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
