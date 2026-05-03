from opcoding.cli import main
import sys

raise SystemExit(main(["ship", *sys.argv[1:]]))
