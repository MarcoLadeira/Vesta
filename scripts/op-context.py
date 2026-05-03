from opcoding.cli import main
import sys

raise SystemExit(main(["context", *sys.argv[1:]]))
