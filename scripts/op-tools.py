from opcoding.cli import main
import sys

raise SystemExit(main(["tools", *sys.argv[1:]]))
