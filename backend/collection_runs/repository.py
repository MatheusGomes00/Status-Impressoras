"""
Repository do domínio Execuções de Coleta.

Responsabilidade única: executar SQL contra a tabela `coleta_executada`.
Não decide status nem regra de negócio - isso é papel do service.py.
"""

import aiomysql

from database import get_connection
from collection_runs.entities import ColetaExecutada, CollectionStatus, TriggerType


async def criar_run(trigger_type: TriggerType) -> int:
    """
    Registra o início de uma coleta (status=RUNNING, contadores zerados)
    e retorna o id gerado, para ser usado pelas leituras dessa execução
    (readings.repository, Fase 3) e para finalizar o run depois.
    """
    async with get_connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "INSERT INTO coleta_executada (trigger_type, iniciado_em, status) "
                "VALUES (%s, NOW(), %s)",
                (trigger_type.value, CollectionStatus.RUNNING.value),
            )
            novo_id = cursor.lastrowid

    return novo_id


async def finalizar_run(
    id_coleta: int,
    status: CollectionStatus,
    total_impressoras: int,
    impressoras_sucesso: int,
    impressora_falha: int,
    error_summary: str | None = None,
) -> None:
    """Atualiza o registro da coleta com o resultado final e marca finalizado_em=NOW()."""
    async with get_connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE coleta_executada "
                "SET status = %s, "
                "    total_impressoras = %s, "
                "    impressoras_sucesso = %s, "
                "    impressora_falha = %s, "
                "    error_summary = %s, "
                "    finalizado_em = NOW() "
                "WHERE id = %s",
                (
                    status.value,
                    total_impressoras,
                    impressoras_sucesso,
                    impressora_falha,
                    error_summary,
                    id_coleta,
                ),
            )


async def buscar_por_id(id_coleta: int) -> ColetaExecutada | None:
    """Busca uma execução de coleta pelo id. Retorna None se não existir."""
    async with get_connection() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                "SELECT id, trigger_type, iniciado_em, finalizado_em, status, "
                "total_impressoras, impressoras_sucesso, impressora_falha, error_summary "
                "FROM coleta_executada "
                "WHERE id = %s",
                (id_coleta,),
            )
            linha = await cursor.fetchone()

    return ColetaExecutada.from_row(linha) if linha else None


async def buscar_ultima() -> ColetaExecutada | None:
    """
    Retorna a execução de coleta mais recente (por iniciado_em).

    Usada pelo endpoint de observabilidade GET /collections/latest
    (Fase 7) e pelo cli.py para mostrar o resultado logo após rodar.
    """
    async with get_connection() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                "SELECT id, trigger_type, iniciado_em, finalizado_em, status, "
                "total_impressoras, impressoras_sucesso, impressora_falha, error_summary "
                "FROM coleta_executada "
                "ORDER BY iniciado_em DESC "
                "LIMIT 1"
            )
            linha = await cursor.fetchone()

    return ColetaExecutada.from_row(linha) if linha else None


async def listar_recentes(limite: int = 20) -> list[ColetaExecutada]:
    """
    Retorna as últimas execuções de coleta, mais recentes primeiro.

    Útil para uma futura tela/relatório de observabilidade mais completa
    que só a última execução.
    """
    async with get_connection() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                "SELECT id, trigger_type, iniciado_em, finalizado_em, status, "
                "total_impressoras, impressoras_sucesso, impressora_falha, error_summary "
                "FROM coleta_executada "
                "ORDER BY iniciado_em DESC "
                "LIMIT %s",
                (limite,),
            )
            linhas = await cursor.fetchall()

    return [ColetaExecutada.from_row(linha) for linha in linhas]
