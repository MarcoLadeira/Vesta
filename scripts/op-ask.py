from opcoding.cli import main
import sys

raise SystemExit(main(["ask", *sys.argv[1:]]))
