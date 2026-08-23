#!/usr/bin/env python3
"""cc2obsidian のエントリポイント。リポジトリを sys.path に通して CLI を呼ぶ。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cc2obsidian.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
