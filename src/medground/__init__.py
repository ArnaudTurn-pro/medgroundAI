"""medground — grounded Graph-RAG over cancer research literature."""

__version__ = "0.0.1"

# Use the system trust store (macOS keychain / Linux ca-certificates / Windows store) so
# corporate MITM proxies and locally-installed CAs work without disabling TLS verification.
# Injected at package import time, before any HTTP client is created.
try:  # pragma: no cover
    import truststore as _truststore

    _truststore.inject_into_ssl()
except Exception:
    # truststore is optional at runtime: if injection fails (e.g. unsupported platform),
    # fall back to certifi defaults rather than crash. We never disable verification.
    pass
