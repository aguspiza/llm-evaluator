"""Shared pytest configuration — adds project root to sys.path."""
import sys
from pathlib import Path

# Ensure the project root is importable regardless of how pytest is invoked
sys.path.insert(0, str(Path(__file__).parent.parent))
