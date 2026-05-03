from opcoding.cli import main
import sys

raise SystemExit(main(["git", *sys.argv[1:]]))
