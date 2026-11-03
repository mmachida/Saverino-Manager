from __future__ import annotations

import os
import subprocess
from pathlib import Path


# Open in explorer.
def open_in_explorer(path: Path) -> None:
    """Abre uma pasta sem interpolar o caminho em um shell."""
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(target)
    if os.name != "nt":
        raise OSError("O Explorador do Windows só está disponível no Windows")
    subprocess.Popen(["explorer.exe", str(target)], close_fds=True)
