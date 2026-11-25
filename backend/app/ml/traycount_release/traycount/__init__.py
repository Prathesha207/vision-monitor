"""TrayCount - ducks / non-ducks on a turning tray: counts, toy IDs, pause while a hand is in the tray, missing toys.
Single entry point: TrayCounter (see traycount/pipeline.py)."""
from .pipeline import TrayCounter

__all__ = ["TrayCounter"]
__version__ = "1.3.0"
