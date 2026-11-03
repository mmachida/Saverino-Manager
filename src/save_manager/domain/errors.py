class DomainError(ValueError):
    """Erro de validação ou regra de negócio."""


class PathPolicyError(DomainError):
    pass


class IntegrityError(DomainError):
    pass


class OperationError(RuntimeError):
    pass
