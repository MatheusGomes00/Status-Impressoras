"""
Repository do domínio Leituras de Toner.

Responsabilidade única: gravar e consultar a tabela `leitura_toner`.
Não decide status nem calcula percentual - isso é papel do service.py.
"""

from datetime import datetime

import aiomysql

from database import get_connection
from readings.entities import LeituraToner

_COLUNAS = (
    "id_leitura, id_impressora, id_coleta_executada, indice_suprimento, "
    "descricao_suprimento, tipo_suprimento, nivel_percentual, nivel_bruto, "
    "capacidade_max, paginas_total, paginas_copias, status, coletado_em"
)


async def inserir_lote(
    id_impressora: int,
    id_coleta_executada: int,
    leituras: list,
) -> list[int]:
    """
    Insere as leituras de uma impressora numa execução de coleta, todas
    na mesma transação, e devolve os ids gerados na ordem de entrada.

    `leituras` é uma lista de LeituraPendente (ver readings/service.py).
    Aceita aqui como objetos com esses atributos (duck typing) em vez
    de importar o tipo, para não criar dependência circular entre
    repository e service.

    Insere linha a linha em vez de usar executemany porque precisamos do
    id de cada leitura (o detector de troca de cartucho referencia a
    leitura exata que registrou o salto de nível). São ~3 linhas por
    impressora, então o custo é irrelevante e a transação continua única.
    """
    if not leituras:
        return []

    ids: list[int] = []

    async with get_connection() as conn:
        async with conn.cursor() as cursor:
            for leitura in leituras:
                await cursor.execute(
                    "INSERT INTO leitura_toner "
                    "(id_impressora, id_coleta_executada, indice_suprimento, "
                    " descricao_suprimento, tipo_suprimento, nivel_percentual, "
                    " nivel_bruto, capacidade_max, paginas_total, paginas_copias, status) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        id_impressora,
                        id_coleta_executada,
                        leitura.indice_suprimento,
                        leitura.descricao_suprimento,
                        leitura.tipo_suprimento,
                        leitura.nivel_percentual,
                        leitura.nivel_bruto,
                        leitura.capacidade_max,
                        leitura.paginas_total,
                        leitura.paginas_copias,
                        leitura.status.value,
                    ),
                )
                ids.append(cursor.lastrowid)

    return ids


async def listar_por_impressora(
    id_impressora: int,
    desde: datetime | None = None,
) -> list[LeituraToner]:
    """
    Retorna o histórico de leituras de uma impressora, em ordem
    cronológica. Se `desde` for informado, filtra a partir dessa data
    (usado pelo relatório semanal e pelo endpoint de histórico da API).
    """
    query = (
        f"SELECT {_COLUNAS} "
        "FROM leitura_toner "
        "WHERE id_impressora = %s "
    )
    parametros: list = [id_impressora]

    if desde is not None:
        query += "AND coletado_em >= %s "
        parametros.append(desde)

    query += "ORDER BY coletado_em ASC, indice_suprimento ASC"

    async with get_connection() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(query, tuple(parametros))
            linhas = await cursor.fetchall()

    return [LeituraToner.from_row(linha) for linha in linhas]


async def buscar_ultimas_por_impressora() -> dict[int, list[LeituraToner]]:
    """
    Retorna, para cada impressora, as leituras (todos os suprimentos)
    da sua execução de coleta mais recente.

    Usa MAX(id_coleta_executada) em vez de MAX(coletado_em) de propósito:
    id_coleta_executada é um FK monotonicamente crescente, então
    identifica a coleta mais recente de forma inequívoca mesmo que
    múltiplas leituras da mesma coleta tenham timestamps idênticos ou
    muito próximos entre si.

    É a base do dashboard "status atual" e do futuro endpoint
    GET /printers - evita uma query por impressora.
    """
    async with get_connection() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                "SELECT lt.id_leitura, lt.id_impressora, lt.id_coleta_executada, "
                "lt.indice_suprimento, lt.descricao_suprimento, lt.tipo_suprimento, "
                "lt.nivel_percentual, lt.nivel_bruto, lt.capacidade_max, "
                "lt.paginas_total, lt.paginas_copias, lt.status, lt.coletado_em "
                "FROM leitura_toner lt "
                "INNER JOIN ( "
                "    SELECT id_impressora, MAX(id_coleta_executada) AS max_coleta "
                "    FROM leitura_toner "
                "    GROUP BY id_impressora "
                ") ultimas "
                "  ON lt.id_impressora = ultimas.id_impressora "
                " AND lt.id_coleta_executada = ultimas.max_coleta "
                "ORDER BY lt.id_impressora, lt.indice_suprimento"
            )
            linhas = await cursor.fetchall()

    resultado: dict[int, list[LeituraToner]] = {}
    for linha in linhas:
        leitura = LeituraToner.from_row(linha)
        resultado.setdefault(leitura.id_impressora, []).append(leitura)

    return resultado


async def buscar_anteriores_por_suprimento(
    id_impressora: int,
    id_coleta_executada: int,
) -> dict[int, LeituraToner]:
    """
    Retorna a última leitura de cada suprimento de uma impressora ANTES
    da coleta informada, indexada por indice_suprimento.

    Só considera leituras com status 'ok': comparar contra uma leitura
    sem medição produziria uma "troca" fantasma toda vez que o cartucho
    paralelo voltasse a reportar nível.
    """
    async with get_connection() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                "SELECT lt.id_leitura, lt.id_impressora, lt.id_coleta_executada, "
                "lt.indice_suprimento, lt.descricao_suprimento, lt.tipo_suprimento, "
                "lt.nivel_percentual, lt.nivel_bruto, lt.capacidade_max, "
                "lt.paginas_total, lt.paginas_copias, lt.status, lt.coletado_em "
                "FROM leitura_toner lt "
                "INNER JOIN ( "
                "    SELECT indice_suprimento, MAX(id_coleta_executada) AS max_coleta "
                "    FROM leitura_toner "
                "    WHERE id_impressora = %s "
                "      AND id_coleta_executada < %s "
                "      AND status = 'ok' "
                "    GROUP BY indice_suprimento "
                ") anteriores "
                "  ON lt.indice_suprimento = anteriores.indice_suprimento "
                " AND lt.id_coleta_executada = anteriores.max_coleta "
                "WHERE lt.id_impressora = %s",
                (id_impressora, id_coleta_executada, id_impressora),
            )
            linhas = await cursor.fetchall()

    return {
        linha["indice_suprimento"]: LeituraToner.from_row(linha) for linha in linhas
    }
