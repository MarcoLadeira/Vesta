from opcoding.cli import main
import sys

task = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "plan current task"
raise SystemExit(main(["route", task]))
