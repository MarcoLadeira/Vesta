from opcoding.cli import main
import sys

raise SystemExit(main(["hooks", *sys.argv[1:]]))
