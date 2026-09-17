"""
Service do Coletor - orquestra uma execução de coleta inteira.

É o único módulo que conhece todos os domínios ao mesmo tempo, e é de
propósito: ele existe justamente para amarrar impressoras, leituras,
execuções de coleta e trocas de toner num fluxo só. Os domínios
continuam sem se conhecer entre si.

Fluxo de uma execução
---------------------
  1. abre um registro em coleta_executada (status RUNNING)
  2. consulta as impressoras ativas em paralelo, limitado por
     COLLECTOR_MAX_CONCURRENT_REQUESTS
  3. para cada impressora: grava as leituras, atualiza marca/modelo e
     detecta troca de cartucho
  4. fecha o registro com COMPLETED / PARTIAL / FAILED

O que conta como sucesso: a impressora ter RESPONDIDO ao SNMP. Uma
leitura com status 'erro' ou 'nao_reportado' não derruba a impressora
para falha - ela respondeu, o consumível é que não informou nível.
Misturar as duas coisas faria a taxa de falha da coleta medir qualidade
de cartucho em vez de saúde da rede.
"""

import asyncio
import logging

from collection_runs.entities import ColetaExecutada, TriggerType
from collection_runs import service as collection_runs_service
from collector.snmp_client import consultar_impressora
from config import settings
from printers import service as printers_service
from printers.entities import Impressora
from readings import service as readings_service
from readings.entities import ReadingStatus, eh_tipo_toner
from toner_changes import service as toner_changes_service

logger = logging.getLogger(__name__)

_LIMITE_ERROR_SUMMARY = 2000


async def _coletar_impressora(
    impressora: Impressora,
    id_coleta: int,
    semaforo: asyncio.Semaphore,
) -> tuple[bool, str | None]:
    """
    Consulta e persiste os dados de UMA impressora.

    Devolve (respondeu, mensagem_de_erro). Nunca levanta exceção: uma
    impressora problemática não pode derrubar a coleta das outras 65,
    então qualquer falha inesperada vira uma linha no error_summary.
    """
    async with semaforo:
        try:
            resposta = await consultar_impressora(impressora.ip_address)
        except Exception as excecao:
            logger.exception("Erro inesperado consultando %s", impressora.ip_address)
            return False, f"{impressora.ip_address}: {type(excecao).__name__}: {excecao}"

    try:
        if not resposta.respondeu:
            await readings_service.salvar_leituras(
                impressora.id_impressora,
                id_coleta,
                [readings_service.montar_leitura_sem_resposta()],
            )
            return False, f"{impressora.ip_address}: {resposta.erro}"

        # Buscar as leituras anteriores ANTES de gravar as novas deixa
        # explícito que a comparação é contra o passado, sem depender do
        # filtro da query para excluir o que acabamos de inserir.
        anteriores = await readings_service.leituras_anteriores_por_suprimento(
            impressora.id_impressora, id_coleta
        )

        leituras = readings_service.montar_leituras_snmp(
            niveis=resposta.niveis,
            capacidades=resposta.capacidades,
            descricoes=resposta.descricoes,
            tipos=resposta.tipos,
            paginas_total=resposta.paginas_total,
            paginas_copias=resposta.paginas_copias,
        )

        if not leituras:
            # Respondeu a alguma consulta, mas não expôs suprimento algum.
            leituras = [readings_service.montar_leitura_sem_resposta()]

        ids = await readings_service.salvar_leituras(
            impressora.id_impressora, id_coleta, leituras
        )

        await printers_service.registrar_identificacao(
            impressora.id_impressora, resposta.marca, resposta.modelo
        )

        await _detectar_trocas(impressora, leituras, ids, anteriores)
        return True, None

    except Exception as excecao:
        logger.exception(
            "Erro ao persistir dados da impressora %s", impressora.ip_address
        )
        return False, f"{impressora.ip_address}: {type(excecao).__name__}: {excecao}"


async def _detectar_trocas(
    impressora: Impressora,
    leituras: list,
    ids: list[int],
    anteriores: dict,
) -> None:
    """
    Compara cada suprimento recém-lido com a leitura anterior do mesmo
    suprimento e registra as trocas de cartucho encontradas.

    Só avalia leituras 'ok' e que sejam de toner: caixa de resíduo tem
    o comportamento inverso (enche em vez de esvaziar) e geraria uma
    "troca" a cada esvaziamento.
    """
    for leitura, id_leitura in zip(leituras, ids):
        if leitura.status is not ReadingStatus.OK:
            continue
        if not eh_tipo_toner(leitura.tipo_suprimento):
            continue

        anterior = anteriores.get(leitura.indice_suprimento)
        if anterior is None:
            continue

        await toner_changes_service.registrar_se_houve_troca(
            id_impressora=impressora.id_impressora,
            indice_suprimento=leitura.indice_suprimento,
            id_leitura_antes=anterior.id_leitura,
            id_leitura_depois=id_leitura,
            nivel_antes=anterior.nivel_percentual,
            nivel_depois=leitura.nivel_percentual,
            paginas_atual=leitura.paginas_total,
            copias_atual=leitura.paginas_copias,
        )


async def executar_coleta(
    trigger_type: TriggerType = TriggerType.SCHEDULED,
    apenas_ips: list[str] | None = None,
) -> ColetaExecutada:
    """
    Executa uma rodada completa de coleta e devolve o registro final.

    `apenas_ips` restringe a rodada a impressoras específicas - usado
    pelo cli.py para diagnosticar uma máquina isolada sem disparar as 66.
    """
    impressoras = await printers_service.listar_impressoras_ativas()

    if apenas_ips:
        alvos = set(apenas_ips)
        impressoras = [i for i in impressoras if i.ip_address in alvos]

    coleta = await collection_runs_service.iniciar_coleta(trigger_type)
    logger.info(
        "Coleta %s iniciada (%s impressoras, até %s em paralelo)",
        coleta.id,
        len(impressoras),
        settings.collector.max_concurrent_requests,
    )

    semaforo = asyncio.Semaphore(settings.collector.max_concurrent_requests)
    resultados = await asyncio.gather(
        *(
            _coletar_impressora(impressora, coleta.id, semaforo)
            for impressora in impressoras
        )
    )

    sucessos = sum(1 for respondeu, _ in resultados if respondeu)
    erros = [mensagem for _, mensagem in resultados if mensagem]

    error_summary = "\n".join(erros)[:_LIMITE_ERROR_SUMMARY] if erros else None

    finalizada = await collection_runs_service.finalizar_coleta(
        id_coleta=coleta.id,
        total_impressoras=len(impressoras),
        impressoras_sucesso=sucessos,
        impressora_falha=len(impressoras) - sucessos,
        error_summary=error_summary,
    )

    logger.info(
        "Coleta %s finalizada: status=%s sucesso=%s falha=%s",
        finalizada.id,
        finalizada.status.value,
        finalizada.impressoras_sucesso,
        finalizada.impressora_falha,
    )
    return finalizada
