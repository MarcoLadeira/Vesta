"""Narrow frozen-entry point for the Vesta QtWebEngine desktop application."""

from vesta.bootstrap import desktop_main


if __name__ == "__main__":
    raise SystemExit(desktop_main())
