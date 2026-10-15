#!/usr/bin/env python3
"""Run offline web/skill tests with an isolated database set before app import."""
import argparse
import os
import sys
import tempfile
import unittest
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--suite", choices=("all", "web", "skills"), default="all")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    scratch = (root / "webapp" / "tests" / ".tmp").resolve()
    if not scratch.is_relative_to(root):
        raise RuntimeError("Test scratch directory must remain in this checkout")
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="runner-", dir=scratch) as folder:
        if not Path(folder).resolve().is_relative_to(scratch):
            raise RuntimeError("Unexpected test directory")
        # app.py creates its default Store at import time. Never let discovery
        # initialize the user's normal research database.
        os.environ["YANZHENG_DB_PATH"] = str(Path(folder) / "import-only.db")
        os.environ["YANZHENG_MODEL_PROVIDER"] = "demo"
        sys.path.insert(0, str(root / "webapp"))
        suite = unittest.TestSuite()
        if args.suite in ("all", "web"):
            suite.addTests(unittest.TestLoader().discover(str(root / "webapp" / "tests"), pattern=args.pattern))
        if args.suite in ("all", "skills"):
            suite.addTests(unittest.TestLoader().discover(str(root / "tests" / "skills"), pattern=args.pattern))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
