"""orgsync — reconcile Directory work items against Ledger accounts.

The package is deliberately thin: `config` loads the service URLs, `models`
holds the record shapes, `http_client` speaks HTTP to one service, and `sync`
is where the reconciliation lives. Command line: `orgsync.__main__`.
"""

__version__ = "0.3.0"
__all__ = ["__version__"]
