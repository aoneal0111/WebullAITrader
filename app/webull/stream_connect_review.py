"""Compatibility entry point for the read-only Webull stream diagnostic.

Production broker composition is owned outside the Webull transport package.
"""
from app.diagnostics.webull_stream_connect_review import check, main

if __name__ == "__main__":
    main()
