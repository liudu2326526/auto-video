"""Compatibility launcher for the experimental, fixture-only browser probe."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from recording_agent.browser_probe import main
if __name__ == "__main__":
    main()
