from opcoding.cli import main
import sys

raise SystemExit(main(["dashboard", *sys.argv[1:]]))
