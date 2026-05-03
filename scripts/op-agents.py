from opcoding.cli import main
import sys

raise SystemExit(main(["agents", *sys.argv[1:]]))
