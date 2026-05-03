from opcoding.cli import main
import sys

raise SystemExit(main(["deploy", *sys.argv[1:]]))
