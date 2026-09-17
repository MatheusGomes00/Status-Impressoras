"""
Repository do domínio Impressoras.

Responsabilidade única: executar SQL contra a tabela `impressora` e
devolver objetos `Impressora`. Não contém regra de negócio - isso
fica no service.py. Se amanhã trocarmos de banco ou de driver, só
este arquivo deveria precisar mudar.
"""

import aiomysql

from database import get_connection
from printers.entities import Impressora

_COLUNAS = (
    "id_impressora, patrimonio, ip_address, location, numero_serie, "
    "marca, modelo, active, criado_em, atualizado_em"
)


async def listar_ativas() -> list[Impressora]:
    """
    Retorna todas as impressoras com active=True.

    Usada pelo coletor: só faz sentido consultar via SNMP as impressoras
    que estão marcadas como ativas no cadastro.
    """
    async with get_connection() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                f"SELECT {_COLUNAS} "
                "FROM impressora "
                "WHERE active = TRUE "
                "ORDER BY location"
            )
            linhas = await cursor.fetchall()

    return [Impressora.from_row(linha) for linha in linhas]


async def buscar_por_id(id_impressora: int) -> Impressora | None:
    """Busca uma impressora pelo id. Retorna None se não existir."""
    async with get_connection() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                f"SELECT {_COLUNAS} FROM impressora WHERE id_impressora = %s",
                (id_impressora,),
            )
            linha = await cursor.fetchone()

    return Impressora.from_row(linha) if linha else None


async def buscar_por_ip(ip_address: str) -> Impressora | None:
    """Busca uma impressora pelo IP. Retorna None se não existir."""
    async with get_connection() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                f"SELECT {_COLUNAS} FROM impressora WHERE ip_address = %s",
                (ip_address,),
            )
            linha = await cursor.fetchone()

    return Impressora.from_row(linha) if linha else None


async def contar_ativas() -> int:
    """
    Retorna quantas impressoras estão ativas.

    Útil para o service do coletor montar o campo `total_impressoras`
    do registro em coleta_executada, sem precisar carregar a lista
    inteira só para contar.
    """
    async with get_connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT COUNT(*) FROM impressora WHERE active = TRUE")
            (total,) = await cursor.fetchone()

    return total


async def atualizar_identificacao(
    id_impressora: int,
    marca: str | None,
    modelo: str | None,
) -> None:
    """
    Grava marca/modelo descobertos via SNMP durante a coleta.

    Usa COALESCE para nunca apagar um valor já conhecido quando a
    impressora deixa de responder aquele OID numa coleta específica -
    identificação de hardware não deveria oscilar por causa de uma
    resposta SNMP incompleta.
    """
    if marca is None and modelo is None:
        return

    async with get_connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE impressora "
                "SET marca = COALESCE(%s, marca), "
                "    modelo = COALESCE(%s, modelo) "
                "WHERE id_impressora = %s",
                (marca, modelo, id_impressora),
            )
