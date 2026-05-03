from opcoding.cli import main
import sys

raise SystemExit(main(["refactor", *sys.argv[1:]]))
