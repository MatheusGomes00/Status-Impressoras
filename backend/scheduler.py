"""
Agendador das coletas automáticas.

Dispara `collector.service.executar_coleta` nos horários configurados em
SCHEDULER_HORARIOS (padrão 09:00, 13:00 e 17:00), todos os dias da semana.

É a mesma função que o cli.py chama. Coleta manual e coleta agendada
percorrem exatamente o mesmo caminho, então não existe a possibilidade de
uma funcionar e a outra não.

Uso:
    python scheduler.py                 # fica em execução até Ctrl+C
    python scheduler.py --agora         # dispara uma coleta na partida também

Proteção contra coleta sobreposta
---------------------------------
São três camadas, e cada uma cobre um cenário diferente:

  1. `max_instances=1` - impede o APScheduler de iniciar um segundo job
     enquanto o anterior não terminou. Cobre o caso de uma coleta
     demorar mais que o intervalo entre horários.

  2. `coalesce=True` - se a máquina hibernar ou o processo ficar parado
     e dois disparos vencerem no intervalo, executa UM só ao voltar, em
     vez de enfileirar as rodadas atrasadas de uma vez.

  3. Índice UNIQUE no banco - impede duas coletas RUNNING mesmo vindas
     de PROCESSOS diferentes (o agendador rodando e alguém executando
     cli.py na mão). É a única camada que funciona entre processos; as
     outras duas vivem na memória deste. Ver o comentário da tabela
     coleta_executada em database/scriptCriarTabelas.sql.

Não há lógica de retry para impressora que não respondeu: a decisão é
medir a taxa de 'sem_resposta' em produção antes de adicionar essa
complexidade. Uma impressora desligada à noite vai falhar de qualquer
jeito, e retry só resolveria falha transitória de rede.
"""

import argparse
import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from collection_runs.entities import ColetaEmAndamento, TriggerType
from collector.service import executar_coleta
from config import settings
from database import close_pool

logger = logging.getLogger(__name__)


async def rodar_coleta_agendada() -> None:
    """
    Job do agendador. Nunca levanta exceção.

    Se uma exceção escapasse daqui, o APScheduler registraria o job como
    falho e, dependendo da configuração, poderia parar de reagendá-lo -
    uma falha de rede às 09h não pode cancelar as coletas das 13h e 17h.
    """
    try:
        coleta = await executar_coleta(trigger_type=TriggerType.SCHEDULED)
        logger.info(
            "Coleta agendada %s concluída: status=%s sucesso=%s falha=%s",
            coleta.id,
            coleta.status.value,
            coleta.impressoras_sucesso,
            coleta.impressora_falha,
        )
    except ColetaEmAndamento:
        logger.warning(
            "Rodada pulada: já havia uma coleta em execução. "
            "Nenhum registro foi criado."
        )
    except Exception:
        logger.exception("Falha inesperada na coleta agendada.")


def montar_agendador() -> AsyncIOScheduler:
    """Monta o agendador com um gatilho diário por horário configurado."""
    agendador = AsyncIOScheduler(timezone=settings.scheduler.timezone)

    for hora, minuto in settings.scheduler.horarios:
        agendador.add_job(
            rodar_coleta_agendada,
            trigger=CronTrigger(hour=hora, minute=minuto),
            id=f"coleta_{hora:02d}{minuto:02d}",
            name=f"Coleta diária das {hora:02d}:{minuto:02d}",
            max_instances=1,
            coalesce=True,
            # Tolera até 10 min de atraso no disparo (máquina ocupada ou
            # relógio ajustado). Depois disso, pular é melhor que rodar
            # uma coleta fora de hora e sujar a série temporal.
            misfire_grace_time=600,
        )

    return agendador


async def _main(disparar_agora: bool) -> None:
    agendador = montar_agendador()
    agendador.start()

    horarios = ", ".join(
        f"{hora:02d}:{minuto:02d}" for hora, minuto in settings.scheduler.horarios
    )
    logger.info(
        "Agendador iniciado: coletas diárias às %s (%s). Ctrl+C para encerrar.",
        horarios,
        settings.scheduler.timezone,
    )

    if disparar_agora:
        await rodar_coleta_agendada()

    try:
        # Mantém o processo vivo sem consumir CPU. O AsyncIOScheduler
        # roda sobre este mesmo event loop, então basta não deixá-lo
        # terminar.
        await asyncio.Event().wait()
    finally:
        agendador.shutdown(wait=True)
        await close_pool()
        logger.info("Agendador encerrado.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Mantém as coletas automáticas rodando nos horários configurados."
    )
    parser.add_argument(
        "--agora",
        action="store_true",
        help="Dispara uma coleta imediatamente ao iniciar, além dos horários.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Mostra o log detalhado de cada coleta.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    try:
        asyncio.run(_main(args.agora))
    except KeyboardInterrupt:
        pass
