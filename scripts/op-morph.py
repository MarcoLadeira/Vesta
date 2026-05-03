from opcoding.cli import main
import sys

raise SystemExit(main(["morph", *sys.argv[1:]]))
