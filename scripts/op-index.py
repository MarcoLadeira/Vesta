from opcoding.cli import main
import sys

raise SystemExit(main(["index", *sys.argv[1:]]))
