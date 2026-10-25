class ServiceError(Exception):
    """Error de negocio con un mensaje para mostrar al usuario y el código HTTP que le corresponde."""

    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code
