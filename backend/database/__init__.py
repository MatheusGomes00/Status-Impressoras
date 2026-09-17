"""
Infraestrutura de acesso ao banco de dados (MariaDB via aiomysql).

Uso recomendado pela maioria dos repositórios:
    from database import get_connection

    async with get_connection() as conn:
        ...
"""

from database.connection import (
    check_connection,
    close_pool,
    get_connection,
    get_pool,
)

__all__ = ["get_connection", "get_pool", "close_pool", "check_connection"]
