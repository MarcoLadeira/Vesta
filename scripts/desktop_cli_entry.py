"""Narrow frozen-entry point for the source-free OPai command line."""

from opai.bootstrap import cli_main


if __name__ == "__main__":
    raise SystemExit(cli_main())
