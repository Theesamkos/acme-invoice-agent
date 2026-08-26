"""Entry point for the Acme invoice processing pipeline.

Usage:
    python main.py --invoice_path=data/invoices/invoice_1001.txt
    python main.py --batch
"""

import sys

from invoice_agent.cli.app import main

if __name__ == "__main__":
    sys.exit(main())
