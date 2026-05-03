from opcoding.cli import main
import sys

raise SystemExit(main(["review", *sys.argv[1:]]))
