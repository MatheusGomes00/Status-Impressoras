"""
Repository do domínio Execuções de Coleta.

Responsabilidade única: executar SQL contra a tabela `coleta_executada`.
Não decide status nem regra de negócio - isso é papel do service.py.
"""

import aiomysql

from database import get_connection
from collection_runs.entities import (
    ColetaEmAndamento,
    ColetaExecutada,
    CollectionStatus,
    TriggerType,
)

_COLUNAS = (
    "id, trigger_type, iniciado_em, finalizado_em, status, "
    "total_impressoras, impressoras_sucesso, impressora_falha, error_summary"
)


async def criar_run(trigger_type: TriggerType) -> int:
    """
    Registra o início de uma coleta (status=RUNNING, contadores zerados)
    e retorna o id gerado, para ser usado pelas leituras dessa execução
    e para finalizar o run depois.

    Levanta ColetaEmAndamento se já houver outra coleta RUNNING. Quem
    barra é o índice UNIQUE uq_uma_coleta_em_execucao, no banco - ver o
    comentário da tabela em database/scriptCriarTabelas.sql. Traduzir o
    erro do driver para uma exceção do domínio é papel do repository:
    é a única camada que deve conhecer o aiomysql.
    """
    try:
        async with get_connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "INSERT INTO coleta_executada (trigger_type, iniciado_em, status) "
                    "VALUES (%s, NOW(), %s)",
                    (trigger_type.value, CollectionStatus.RUNNING.value),
                )
                return cursor.lastrowid
    except aiomysql.IntegrityError as excecao:
        if "uq_uma_coleta_em_execucao" in str(excecao):
            raise ColetaEmAndamento(
                "Já existe uma coleta em execução."
            ) from excecao
        raise


async def buscar_em_execucao() -> ColetaExecutada | None:
    """Retorna a coleta com status RUNNING, se houver. No máximo uma existe."""
    async with get_connection() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                f"SELECT {_COLUNAS} "
                "FROM coleta_executada "
                "WHERE status = %s "
                "LIMIT 1",
                (CollectionStatus.RUNNING.value,),
            )
            linha = await cursor.fetchone()

    return ColetaExecutada.from_row(linha) if linha else None


async def marcar_travada_como_falha(id_coleta: int, motivo: str) -> None:
    """
    Fecha como FAILED uma coleta que ficou presa em RUNNING.

    Só faz sentido para o caso de o processo ter morrido no meio (queda
    de energia, reboot): sem isso, a linha RUNNING órfã bloquearia todas
    as coletas seguintes por causa do índice UNIQUE.

    Preserva os contadores já gravados e só acrescenta o motivo ao
    error_summary - a coleta rodou parcialmente, e apagar o que ela
    conseguiu registrar seria perder informação.
    """
    async with get_connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE coleta_executada "
                "SET status = %s, "
                "    finalizado_em = NOW(), "
                "    error_summary = CONCAT(COALESCE(error_summary, ''), %s) "
                "WHERE id = %s AND status = %s",
                (
                    CollectionStatus.FAILED.value,
                    f"\n{motivo}",
                    id_coleta,
                    CollectionStatus.RUNNING.value,
                ),
            )


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
                f"SELECT {_COLUNAS} "
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
                f"SELECT {_COLUNAS} "
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
                f"SELECT {_COLUNAS} "
                "FROM coleta_executada "
                "ORDER BY iniciado_em DESC "
                "LIMIT %s",
                (limite,),
            )
            linhas = await cursor.fetchall()

    return [ColetaExecutada.from_row(linha) for linha in linhas]
