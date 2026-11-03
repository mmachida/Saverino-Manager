"""Serviços de consulta, validação e instalação de atualizações."""

from .config import UpdateConfig
from .installer import check_install_prerequisites, confirm_pending_startup, ensure_initial_state
from .service import ReleaseInfo, UpdateError, UpdateService

__all__ = [
    "ReleaseInfo",
    "UpdateConfig",
    "UpdateError",
    "UpdateService",
    "check_install_prerequisites",
    "confirm_pending_startup",
    "ensure_initial_state",
]
