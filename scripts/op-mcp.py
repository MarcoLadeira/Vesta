from opcoding.cli import main
import sys

raise SystemExit(main(["mcp", *sys.argv[1:]]))
