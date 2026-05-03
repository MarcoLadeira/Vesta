from opcoding.cli import main
import sys

raise SystemExit(main(["scan", *sys.argv[1:]]))
