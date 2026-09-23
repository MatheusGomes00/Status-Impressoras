"""
Ponto de entrada de linha de comando do coletor.

É o teste_snmp.py "crescido": em vez de consultar uma impressora e
imprimir na tela, roda o ciclo completo - abre um registro em
coleta_executada, percorre as impressoras ativas, grava as leituras,
detecta trocas de cartucho e fecha o registro com o status final.

Para que serve:
  - rodar a coleta de ponta a ponta antes de existir agendador e API
  - disparar uma coleta sob demanda (ex: acabou de trocar um toner)
  - diagnosticar uma impressora isolada, sem mexer nas outras 65

Não contém regra de negócio: é uma casca fina sobre
collector.service.executar_coleta. O agendador vai chamar exatamente a
mesma função, o que garante que "rodar na mão" e "rodar agendado"
façam a mesma coisa.

Uso:
    python cli.py                              # coleta completa (MANUAL)
    python cli.py --ip 10.165.20.133           # só uma impressora
    python cli.py --ip 10.165.20.133 --dry-run # consulta e mostra, sem gravar
    python cli.py --ultima                     # só mostra o resultado da última coleta
    python cli.py --agendada                   # registra como SCHEDULED

Código de saída: 0 em sucesso ou coleta parcial, 1 quando a coleta
termina em FAILED. É o que permite ao Agendador de Tarefas do Windows
sinalizar a rodada como falha, caso esse seja o modo de execução
escolhido para produção (ver docs/CRONOGRAMA.md, Fase 6).
"""

import argparse
import asyncio
import logging
import sys

from collection_runs import service as collection_runs_service
from collection_runs.entities import (
    ColetaEmAndamento,
    ColetaExecutada,
    CollectionStatus,
    TriggerType,
)
from collector.service import executar_coleta
from collector.snmp_client import consultar_impressora
from database import close_pool
from printers import service as printers_service
from readings import service as readings_service


def _mostrar_coleta(coleta: ColetaExecutada | None) -> None:
    if coleta is None:
        print("Nenhuma coleta registrada ainda.")
        return

    print(
        f"\nColeta #{coleta.id} [{coleta.trigger_type.value}] -> {coleta.status.value}\n"
        f"  início:     {coleta.iniciado_em}\n"
        f"  fim:        {coleta.finalizado_em}\n"
        f"  impressoras: {coleta.total_impressoras} "
        f"(sucesso={coleta.impressoras_sucesso}, falha={coleta.impressora_falha})"
    )
    if coleta.error_summary:
        print("  erros:")
        for linha in coleta.error_summary.splitlines():
            print(f"    - {linha}")


async def _dry_run(ips: list[str] | None) -> None:
    """
    Consulta as impressoras e imprime o que SERIA gravado, sem tocar no banco.

    Serve para conferir o pareamento dos suprimentos contra o painel da
    impressora antes de confiar nos dados do relatório.
    """
    impressoras = await printers_service.listar_impressoras_ativas()
    if ips:
        alvos = set(ips)
        impressoras = [i for i in impressoras if i.ip_address in alvos]

    if not impressoras:
        print("Nenhuma impressora ativa corresponde ao filtro informado.")
        return

    for impressora in impressoras:
        resposta = await consultar_impressora(impressora.ip_address)
        print(f"\n{impressora.ip_address} - {impressora.location}")

        if not resposta.respondeu:
            print(f"  SEM RESPOSTA: {resposta.erro}")
            continue

        print(f"  série:    {resposta.numero_serie}")
        print(f"  marca:    {resposta.marca}")
        print(f"  modelo:   {resposta.modelo}")
        print(f"  páginas:  total={resposta.paginas_total} copias={resposta.paginas_copias}")

        leituras = readings_service.montar_leituras_snmp(
            niveis=resposta.niveis,
            capacidades=resposta.capacidades,
            descricoes=resposta.descricoes,
            tipos=resposta.tipos,
            paginas_total=resposta.paginas_total,
            paginas_copias=resposta.paginas_copias,
        )
        for leitura in leituras:
            percentual = (
                f"{leitura.nivel_percentual}%"
                if leitura.nivel_percentual is not None
                else "N/D"
            )
            print(
                f"  [{leitura.indice_suprimento}] {leitura.descricao_suprimento or '(sem descrição)'} "
                f"tipo={leitura.tipo_suprimento} nível={percentual} "
                f"(bruto={leitura.nivel_bruto}/{leitura.capacidade_max}) "
                f"status={leitura.status.value}"
            )


async def _main(args: argparse.Namespace) -> int:
    """
    Devolve o código de saída do processo.

    Sai com 1 quando a coleta termina em FAILED, para que o Agendador de
    Tarefas do Windows consiga sinalizar a rodada como falha em vez de
    registrar sucesso silencioso. PARTIAL sai com 0: algumas impressoras
    desligadas é o dia a dia normal, não um erro que mereça alarme.
    """
    try:
        if args.ultima:
            _mostrar_coleta(await collection_runs_service.obter_ultima_coleta())
            return 0

        if args.dry_run:
            await _dry_run(args.ip)
            return 0

        try:
            coleta = await executar_coleta(
                trigger_type=(
                    TriggerType.SCHEDULED if args.agendada else TriggerType.MANUAL
                ),
                apenas_ips=args.ip,
            )
        except ColetaEmAndamento:
            em_andamento = await collection_runs_service.obter_coleta_em_andamento()
            print(
                "Já existe uma coleta em execução - nada foi gravado.\n"
                "Provavelmente o agendador disparou agora. Aguarde ela terminar."
            )
            _mostrar_coleta(em_andamento)
            return 0

        _mostrar_coleta(coleta)
        return 1 if coleta.status is CollectionStatus.FAILED else 0
    finally:
        await close_pool()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Executa uma coleta manual de níveis de toner."
    )
    parser.add_argument(
        "--ip",
        action="append",
        help="Restringe a coleta a este IP (pode repetir a opção).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Consulta via SNMP e mostra o resultado, sem gravar no banco.",
    )
    parser.add_argument(
        "--ultima",
        action="store_true",
        help="Apenas exibe o resultado da última coleta registrada.",
    )
    parser.add_argument(
        "--agendada",
        action="store_true",
        help=(
            "Registra a coleta como SCHEDULED em vez de MANUAL. Use quando "
            "quem dispara é o Agendador de Tarefas do Windows, para que o "
            "histórico não classifique a rodada como feita à mão."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Mostra o log detalhado da coleta.",
    )
    argumentos = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if argumentos.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    sys.exit(asyncio.run(_main(argumentos)))
