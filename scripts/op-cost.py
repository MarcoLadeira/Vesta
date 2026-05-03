from opcoding.cli import main
import sys

raise SystemExit(main(["cost", *sys.argv[1:]]))
