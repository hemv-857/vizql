"""vizql - Visual SQL Explainer

See your SQL run. Paste SQL, get an animated dataflow diagram
that shows exactly how the database executes it.
"""

__version__ = "0.1.0"
__author__ = "Hemang Varshney"

from .cli import app

__all__ = ["app"]