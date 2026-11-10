"""CLI entrypoint, also executable by absolute path from a Herdr workspace."""
from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from herdr_claude_accounts.manager import main

if __name__ == '__main__':
    sys.exit(main())
