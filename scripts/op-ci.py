from opcoding.cli import main
import sys

raise SystemExit(main(["ci", *sys.argv[1:]]))
