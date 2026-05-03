from opcoding.cli import main
import sys

raise SystemExit(main(["doctor", *sys.argv[1:]]))
