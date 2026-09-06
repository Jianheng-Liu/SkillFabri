"""WSGI entry point for the deployed site.

`app.py`'s __main__ block parses argv and calls app.run(); a WSGI server imports the module
instead, so the corpus has to be loaded here, at import time, before the first request.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))   # so `import auth, store` resolves

from explorer.app import app, load

load()

