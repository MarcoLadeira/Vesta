from opcoding.cli import main
import sys

raise SystemExit(main(["init", *sys.argv[1:]]))
