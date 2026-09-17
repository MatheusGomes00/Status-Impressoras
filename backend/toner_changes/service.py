"""
Service do domínio Trocas de Toner.

Aqui mora a regra que decide o que é uma troca de cartucho. O
repository só grava o evento; quem interpreta o salto de nível é
este arquivo.

Regra de decisão
----------------
Uma troca é detectada quando o nível de um suprimento SOBE entre duas
leituras consecutivas, acima de um limiar em pontos percentuais
(COLLECTOR_LIMIAR_TROCA_TONER_PP, padrão 20).

O limiar existe porque o nível reportado não é uma medição exata: ele
oscila alguns pontos para cima sem que nada tenha sido trocado. Sem
limiar, o relatório contaria dezenas de trocas fantasma por mês.

Só leituras com status 'ok' entram na comparação - ver
readings.repository.buscar_anteriores_por_suprimento.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal

from config import settings
from toner_changes import repository
from toner_changes.entities import TrocaToner

logger = logging.getLogger(__name__)


@dataclass
class TrocaPendente:
    """Troca já detectada e com o rendimento calculado, pronta para persistir."""
    id_impressora: int
    indice_suprimento: int
    id_leitura_antes: int | None
    id_leitura_depois: int
    nivel_antes: Decimal | None
    nivel_depois: Decimal | None
    paginas_no_evento: int | None
    paginas_rendidas: int | None
    copias_no_evento: int | None
    copias_rendidas: int | None


def houve_troca(nivel_antes: Decimal | None, nivel_depois: Decimal | None) -> bool:
    """
    True quando o salto de nível caracteriza uma troca de cartucho.

    Sem nível anterior não há como afirmar nada: a primeira leitura de
    uma impressora nunca é tratada como troca, senão toda impressora
    nova geraria um evento falso na primeira coleta.
    """
    if nivel_antes is None or nivel_depois is None:
        return False

    return (nivel_depois - nivel_antes) >= settings.collector.limiar_troca_toner_pp


def _rendimento(contador_atual: int | None, contador_troca_anterior: int | None) -> int | None:
    """
    Quantas páginas/cópias o cartucho substituído rendeu.

    Retorna None na primeira troca de um suprimento (não há marco
    anterior) e também se o contador regredir - o que indica reset de
    contador na impressora, e um número negativo aqui seria pior que
    a ausência do dado no relatório.
    """
    if contador_atual is None or contador_troca_anterior is None:
        return None

    rendimento = contador_atual - contador_troca_anterior
    return rendimento if rendimento >= 0 else None


async def registrar_se_houve_troca(
    id_impressora: int,
    indice_suprimento: int,
    id_leitura_antes: int | None,
    id_leitura_depois: int,
    nivel_antes: Decimal | None,
    nivel_depois: Decimal | None,
    paginas_atual: int | None,
    copias_atual: int | None,
) -> TrocaPendente | None:
    """
    Avalia um suprimento e, se houve troca, persiste o evento já com o
    rendimento do cartucho anterior calculado.

    Retorna a troca registrada, ou None se não houve troca.
    """
    if not houve_troca(nivel_antes, nivel_depois):
        return None

    ultima = await repository.buscar_ultima(id_impressora, indice_suprimento)

    troca = TrocaPendente(
        id_impressora=id_impressora,
        indice_suprimento=indice_suprimento,
        id_leitura_antes=id_leitura_antes,
        id_leitura_depois=id_leitura_depois,
        nivel_antes=nivel_antes,
        nivel_depois=nivel_depois,
        paginas_no_evento=paginas_atual,
        paginas_rendidas=_rendimento(
            paginas_atual, ultima.paginas_no_evento if ultima else None
        ),
        copias_no_evento=copias_atual,
        copias_rendidas=_rendimento(
            copias_atual, ultima.copias_no_evento if ultima else None
        ),
    )

    await repository.inserir(troca)
    logger.info(
        "Troca de toner detectada: impressora=%s suprimento=%s "
        "nivel %s%% -> %s%% (rendeu %s páginas)",
        id_impressora,
        indice_suprimento,
        nivel_antes,
        nivel_depois,
        troca.paginas_rendidas,
    )
    return troca


async def historico_da_impressora(id_impressora: int) -> list[TrocaToner]:
    """Retorna todas as trocas registradas para uma impressora."""
    return await repository.listar_por_impressora(id_impressora)


async def trocas_no_periodo(desde=None, ate=None) -> list[TrocaToner]:
    """Retorna as trocas de um intervalo - base do relatório de consumo."""
    return await repository.listar_por_periodo(desde, ate)
