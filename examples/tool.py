"""Deterministic fixture executable with a deliberately small command set."""

import argparse
import sys
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("command", choices=["echo", "sum", "fail", "write", "sleep"])
parser.add_argument("value", nargs="?", default="hello")
parser.add_argument("number", nargs="?", default="0")
args = parser.parse_args()
if args.command == "echo":
    print(args.value)
elif args.command == "sum":
    print(int(args.value) + int(args.number))
elif args.command == "fail":
    print("expected diagnostic", file=sys.stderr)
    sys.exit(7)
elif args.command == "write":
    Path("output.txt").write_text(args.value, encoding="utf-8")
    print("written")
elif args.command == "sleep":
    time.sleep(min(float(args.value), 10))
