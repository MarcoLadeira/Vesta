from opcoding.cli import main
import sys

raise SystemExit(main(["test", *sys.argv[1:]]))
