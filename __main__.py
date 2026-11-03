"""Atalho para executar a aplicação a partir da raiz do projeto."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

if __name__ == "__main__":
    from save_manager.__main__ import main

    main()
