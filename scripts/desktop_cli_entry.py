"""Narrow frozen-entry point for the source-free Vesta command line."""

from vesta.bootstrap import cli_main


if __name__ == "__main__":
    raise SystemExit(cli_main())
