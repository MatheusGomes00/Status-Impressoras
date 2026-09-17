"""
Gerencia o pool de conexões assíncrono com o MariaDB, usando aiomysql.

Cumpre o mesmo papel que o HikariCP teria em Java: mantém um conjunto
de conexões já abertas e prontas para reuso, evitando o custo de abrir
e fechar uma conexão TCP + autenticação a cada operação no banco -
importante aqui porque a coleta grava dados de até 67 impressoras em
sequência/paralelo, várias vezes ao dia.

Uso recomendado (na maioria dos repositórios):
    from database.connection import get_connection

    async with get_connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("INSERT INTO impressora (...) VALUES (...)")
        # commit automático aqui, se nenhuma exceção foi lançada

Uso de baixo nível (casos que precisam controlar a transação manualmente):
    from database.connection import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        ...
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import aiomysql

from config import settings

logger = logging.getLogger(__name__)

_pool: aiomysql.Pool | None = None
_pool_lock = asyncio.Lock()


async def get_pool() -> aiomysql.Pool:
    """
    Retorna o pool de conexões, criando-o na primeira chamada.

    Usa um lock para evitar que duas chamadas concorrentes tentem criar
    o pool ao mesmo tempo (condição de corrida), o que poderia acontecer
    se várias operações dispararem em paralelo logo no início da coleta.
    """
    global _pool

    if _pool is not None:
        return _pool

    async with _pool_lock:
        # Checa de novo depois de obter o lock: outra chamada pode ter
        # criado o pool enquanto essa esperava a vez (double-checked locking).
        if _pool is not None:
            return _pool

        try:
            _pool = await aiomysql.create_pool(
                host=settings.database.host,
                port=settings.database.port,
                user=settings.database.user,
                password=settings.database.password,
                db=settings.database.database,
                minsize=settings.database.pool_min_size,
                maxsize=settings.database.pool_max_size,
                autocommit=False,
                charset="utf8mb4",
            )
            logger.info(
                "Pool de conexões criado: %s@%s:%s/%s (min=%s, max=%s)",
                settings.database.user,
                settings.database.host,
                settings.database.port,
                settings.database.database,
                settings.database.pool_min_size,
                settings.database.pool_max_size,
            )
        except Exception:
            logger.exception("Falha ao criar pool de conexões com o banco.")
            raise

        return _pool


@asynccontextmanager
async def get_connection() -> AsyncIterator[aiomysql.Connection]:
    """
    Empresta uma conexão do pool dentro de um bloco `async with`, com
    commit automático ao final se tudo correr bem, ou rollback automático
    se uma exceção for lançada dentro do bloco.

    Isso evita repetir try/except/commit/rollback em cada repositório -
    quem usa essa função só precisa se preocupar com o SQL em si.
    """
    pool = await get_pool()
    conn = await pool.acquire()
    try:
        yield conn
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise
    finally:
        pool.release(conn)


async def close_pool() -> None:
    """Fecha o pool de conexões de forma organizada (chamar ao encerrar a aplicação)."""
    global _pool
    if _pool is None:
        return

    _pool.close()
    await _pool.wait_closed()
    _pool = None
    logger.info("Pool de conexões fechado.")


async def check_connection() -> bool:
    """
    Testa se é possível abrir uma conexão e rodar uma query simples.
    Útil para smoke tests manuais e para um futuro endpoint de healthcheck.
    """
    try:
        async with get_connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("SELECT 1")
                await cursor.fetchone()
        return True
    except Exception:
        logger.exception("Healthcheck de banco falhou.")
        return False


if __name__ == "__main__":
    # Smoke test manual: roda "python database/connection.py" para
    # confirmar que a conexão com o MariaDB está funcionando de ponta a ponta.
    async def _main():
        ok = await check_connection()
        if ok:
            print("Conexão com o banco funcionando corretamente.")
        else:
            print("Falha ao conectar com o banco. Veja o log acima.")
        await close_pool()

    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())
