from opcoding.cli import main
import sys

raise SystemExit(main(["fix", *sys.argv[1:]]))
