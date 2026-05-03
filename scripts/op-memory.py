from opcoding.cli import main
import sys

raise SystemExit(main(["memory", *sys.argv[1:]]))
