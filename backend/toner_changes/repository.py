"""
Repository do domínio Trocas de Toner.

Responsabilidade única: gravar e consultar a tabela `troca_toner`.
Não decide o que é uma troca - isso é papel do service.py.
"""

from datetime import datetime

import aiomysql

from database import get_connection
from toner_changes.entities import TrocaToner

_COLUNAS = (
    "id_troca, id_impressora, indice_suprimento, id_leitura_antes, "
    "id_leitura_depois, detectado_em, nivel_antes, nivel_depois, "
    "paginas_no_evento, paginas_rendidas, copias_no_evento, copias_rendidas"
)


async def inserir(troca) -> int:
    """
    Registra uma troca detectada e devolve o id gerado.

    `troca` é uma TrocaPendente (ver toner_changes/service.py), aceita
    por duck typing para não criar dependência circular.
    """
    async with get_connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "INSERT INTO troca_toner "
                "(id_impressora, indice_suprimento, id_leitura_antes, "
                " id_leitura_depois, nivel_antes, nivel_depois, "
                " paginas_no_evento, paginas_rendidas, "
                " copias_no_evento, copias_rendidas) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    troca.id_impressora,
                    troca.indice_suprimento,
                    troca.id_leitura_antes,
                    troca.id_leitura_depois,
                    troca.nivel_antes,
                    troca.nivel_depois,
                    troca.paginas_no_evento,
                    troca.paginas_rendidas,
                    troca.copias_no_evento,
                    troca.copias_rendidas,
                ),
            )
            return cursor.lastrowid


async def buscar_ultima(id_impressora: int, indice_suprimento: int) -> TrocaToner | None:
    """
    Retorna a troca mais recente de um suprimento específico.

    É o ponto de partida para calcular `paginas_rendidas` da próxima
    troca: o rendimento do cartucho atual é a diferença entre o contador
    de agora e o contador congelado na troca anterior.
    """
    async with get_connection() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                f"SELECT {_COLUNAS} "
                "FROM troca_toner "
                "WHERE id_impressora = %s AND indice_suprimento = %s "
                "ORDER BY detectado_em DESC, id_troca DESC "
                "LIMIT 1",
                (id_impressora, indice_suprimento),
            )
            linha = await cursor.fetchone()

    return TrocaToner.from_row(linha) if linha else None


async def listar_por_periodo(
    desde: datetime | None = None,
    ate: datetime | None = None,
) -> list[TrocaToner]:
    """
    Retorna as trocas detectadas num intervalo, mais recentes primeiro.

    É a consulta que sustenta o relatório de consumo: quantos cartuchos
    cada impressora consumiu no período e quantas páginas cada um rendeu.
    """
    query = f"SELECT {_COLUNAS} FROM troca_toner WHERE 1 = 1 "
    parametros: list = []

    if desde is not None:
        query += "AND detectado_em >= %s "
        parametros.append(desde)
    if ate is not None:
        query += "AND detectado_em <= %s "
        parametros.append(ate)

    query += "ORDER BY detectado_em DESC, id_troca DESC"

    async with get_connection() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(query, tuple(parametros))
            linhas = await cursor.fetchall()

    return [TrocaToner.from_row(linha) for linha in linhas]


async def listar_por_impressora(id_impressora: int) -> list[TrocaToner]:
    """Retorna o histórico completo de trocas de uma impressora."""
    async with get_connection() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                f"SELECT {_COLUNAS} "
                "FROM troca_toner "
                "WHERE id_impressora = %s "
                "ORDER BY detectado_em DESC, id_troca DESC",
                (id_impressora,),
            )
            linhas = await cursor.fetchall()

    return [TrocaToner.from_row(linha) for linha in linhas]
