"""
Teste de integração do coletor, sem parque e sem banco.

O que este arquivo cobre é a ORQUESTRAÇÃO: quem é contado como sucesso,
o que acontece quando uma impressora não responde, e em que condições a
detecção de troca é acionada. A consulta SNMP e os repositories são
substituídos por dublês.

Por que dublar em vez de usar o parque
--------------------------------------
Um teste que depende das 66 impressoras só roda dentro da rede do
hospital, falha quando alguém desliga uma máquina e não consegue
reproduzir um cenário sob demanda - não dá para pedir a uma impressora
que simule uma troca de cartucho no meio do expediente. Dublando,
estes cenários viram testes determinísticos que rodam em qualquer
máquina, inclusive na de desenvolvimento, que não alcança o parque.

Isso NÃO substitui a validação em hardware: ela continua necessária
para confirmar OIDs e o comportamento real das impressoras, e está
registrada como fase própria em docs/CRONOGRAMA.md.
"""

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from collection_runs.entities import (
    ColetaEmAndamento,
    ColetaExecutada,
    CollectionStatus,
    TriggerType,
)
from collector.service import executar_coleta
from collector.snmp_client import ColetaSnmp
from printers.entities import Impressora
from readings.entities import LeituraToner, ReadingStatus


def criar_impressora(id_impressora: int, ip: str) -> Impressora:
    return Impressora(
        id_impressora=id_impressora,
        patrimonio=f"LOC{id_impressora}",
        ip_address=ip,
        location=f"SETOR {id_impressora}",
        numero_serie=f"SERIE{id_impressora}",
        marca=None,
        modelo=None,
        active=True,
        criado_em=datetime(2026, 1, 1),
        atualizado_em=datetime(2026, 1, 1),
    )


def criar_coleta(id_coleta: int = 1) -> ColetaExecutada:
    return ColetaExecutada(
        id=id_coleta,
        trigger_type=TriggerType.MANUAL,
        iniciado_em=datetime(2026, 1, 1, 9, 0),
        finalizado_em=None,
        status=CollectionStatus.RUNNING,
        total_impressoras=0,
        impressoras_sucesso=0,
        impressora_falha=0,
        error_summary=None,
    )


def criar_resposta_snmp(nivel: str = "850", tipo: str = "3") -> ColetaSnmp:
    return ColetaSnmp(
        respondeu=True,
        numero_serie="35C78453",
        marca="Canon",
        modelo="iR-ADV 4525",
        niveis={"1.1": nivel},
        capacidades={"1.1": "1000"},
        descricoes={"1.1": "Black Toner"},
        tipos={"1.1": tipo},
        paginas_total=50000,
        paginas_copias=None,
    )


def criar_leitura_anterior(nivel: Decimal | None, indice: int = 1) -> LeituraToner:
    return LeituraToner(
        id_leitura=99,
        id_impressora=1,
        id_coleta_executada=1,
        indice_suprimento=indice,
        descricao_suprimento="Black Toner",
        tipo_suprimento=3,
        nivel_percentual=nivel,
        nivel_bruto=50,
        capacidade_max=1000,
        paginas_total=47500,
        paginas_copias=None,
        status=ReadingStatus.OK,
        coletado_em=datetime(2026, 1, 1, 8, 0),
    )


class ColetorDublado:
    """
    Agrupa os patches do collector.service para não repetir seis
    `with patch(...)` aninhados em cada teste.
    """

    def __init__(self, impressoras, resposta_snmp, anteriores=None):
        self.impressoras = impressoras
        self.resposta_snmp = resposta_snmp
        self.anteriores = anteriores or {}
        self.patches = []

    def __enter__(self):
        self.consultar = AsyncMock(
            side_effect=lambda ip: (
                self.resposta_snmp(ip)
                if callable(self.resposta_snmp)
                else self.resposta_snmp
            )
        )
        self.iniciar = AsyncMock(return_value=criar_coleta())
        self.finalizar = AsyncMock(return_value=criar_coleta())
        self.salvar = AsyncMock(side_effect=lambda i, c, leituras: list(range(len(leituras))))
        self.buscar_anteriores = AsyncMock(return_value=self.anteriores)
        self.registrar_identificacao = AsyncMock()
        self.registrar_troca = AsyncMock(return_value=None)

        alvos = {
            "collector.service.consultar_impressora": self.consultar,
            "collector.service.printers_service.listar_impressoras_ativas":
                AsyncMock(return_value=self.impressoras),
            "collector.service.printers_service.registrar_identificacao":
                self.registrar_identificacao,
            "collector.service.collection_runs_service.iniciar_coleta": self.iniciar,
            "collector.service.collection_runs_service.finalizar_coleta": self.finalizar,
            "collector.service.readings_service.salvar_leituras": self.salvar,
            "collector.service.readings_service.leituras_anteriores_por_suprimento":
                self.buscar_anteriores,
            "collector.service.toner_changes_service.registrar_se_houve_troca":
                self.registrar_troca,
        }
        for alvo, dublê in alvos.items():
            p = patch(alvo, dublê)
            p.start()
            self.patches.append(p)
        return self

    def __exit__(self, *args):
        for p in self.patches:
            p.stop()

    @property
    def contagem_final(self) -> dict:
        """Argumentos com que finalizar_coleta foi chamado."""
        return self.finalizar.call_args.kwargs


class TestContagemDeSucesso:
    async def test_todas_responderam(self):
        impressoras = [criar_impressora(1, "10.0.0.1"), criar_impressora(2, "10.0.0.2")]
        with ColetorDublado(impressoras, criar_resposta_snmp()) as dublê:
            await executar_coleta(TriggerType.MANUAL)

        assert dublê.contagem_final["total_impressoras"] == 2
        assert dublê.contagem_final["impressoras_sucesso"] == 2
        assert dublê.contagem_final["impressora_falha"] == 0

    async def test_impressora_sem_resposta_conta_como_falha(self):
        impressoras = [criar_impressora(1, "10.0.0.1")]
        sem_resposta = ColetaSnmp(respondeu=False, erro="timeout")

        with ColetorDublado(impressoras, sem_resposta) as dublê:
            await executar_coleta(TriggerType.MANUAL)

        assert dublê.contagem_final["impressoras_sucesso"] == 0
        assert dublê.contagem_final["impressora_falha"] == 1
        assert "timeout" in dublê.contagem_final["error_summary"]

    async def test_sem_resposta_ainda_grava_a_leitura(self):
        # A ausência precisa ficar no histórico, senão não há como medir
        # a taxa de 'sem_resposta' do parque depois.
        impressoras = [criar_impressora(1, "10.0.0.1")]
        with ColetorDublado(impressoras, ColetaSnmp(respondeu=False, erro="timeout")) as d:
            await executar_coleta(TriggerType.MANUAL)

        leituras_gravadas = d.salvar.call_args.args[2]
        assert leituras_gravadas[0].status is ReadingStatus.SEM_RESPOSTA

    async def test_resposta_parcial_conta_como_sucesso_e_fica_no_resumo(self):
        # Gravou o que era essencial, então é sucesso; mas o campo em
        # branco não pode virar um NULL sem explicação.
        impressoras = [criar_impressora(1, "10.0.0.1")]
        resposta = criar_resposta_snmp()
        resposta.consultas_em_branco = ["sys_descr"]

        with ColetorDublado(impressoras, resposta) as dublê:
            await executar_coleta(TriggerType.MANUAL)

        assert dublê.contagem_final["impressoras_sucesso"] == 1
        assert dublê.contagem_final["impressora_falha"] == 0
        assert "10.0.0.1: resposta parcial, em branco: sys_descr" in (
            dublê.contagem_final["error_summary"]
        )

    async def test_uma_impressora_com_erro_nao_derruba_as_outras(self):
        impressoras = [
            criar_impressora(1, "10.0.0.1"),
            criar_impressora(2, "10.0.0.2"),
            criar_impressora(3, "10.0.0.3"),
        ]

        def resposta_por_ip(ip):
            if ip == "10.0.0.2":
                raise OSError("rede inacessível")
            return criar_resposta_snmp()

        with ColetorDublado(impressoras, resposta_por_ip) as dublê:
            await executar_coleta(TriggerType.MANUAL)

        assert dublê.contagem_final["impressoras_sucesso"] == 2
        assert dublê.contagem_final["impressora_falha"] == 1

    async def test_toner_paralelo_nao_conta_como_falha_da_impressora(self):
        # A impressora respondeu; quem não informou nível foi o cartucho.
        # Contar como falha faria a métrica medir qualidade de consumível
        # em vez de saúde da rede.
        impressoras = [criar_impressora(1, "10.0.0.1")]
        with ColetorDublado(impressoras, criar_resposta_snmp(nivel="-2")) as dublê:
            await executar_coleta(TriggerType.MANUAL)

        assert dublê.contagem_final["impressoras_sucesso"] == 1
        leituras = dublê.salvar.call_args.args[2]
        assert leituras[0].status is ReadingStatus.NAO_REPORTADO


class TestFiltroDeIps:
    async def test_apenas_ips_restringe_a_rodada(self):
        impressoras = [criar_impressora(1, "10.0.0.1"), criar_impressora(2, "10.0.0.2")]
        with ColetorDublado(impressoras, criar_resposta_snmp()) as dublê:
            await executar_coleta(TriggerType.MANUAL, apenas_ips=["10.0.0.2"])

        assert dublê.contagem_final["total_impressoras"] == 1
        dublê.consultar.assert_awaited_once_with("10.0.0.2")


class TestDeteccaoDeTroca:
    async def test_nivel_que_sobe_dispara_a_deteccao(self):
        impressoras = [criar_impressora(1, "10.0.0.1")]
        anteriores = {1: criar_leitura_anterior(Decimal("5"))}

        with ColetorDublado(impressoras, criar_resposta_snmp(), anteriores) as dublê:
            await executar_coleta(TriggerType.MANUAL)

        dublê.registrar_troca.assert_awaited_once()
        argumentos = dublê.registrar_troca.call_args.kwargs
        assert argumentos["nivel_antes"] == Decimal("5")
        assert argumentos["nivel_depois"] == Decimal("85.0")
        assert argumentos["paginas_atual"] == 50000

    async def test_primeira_coleta_nao_dispara_deteccao(self):
        impressoras = [criar_impressora(1, "10.0.0.1")]
        with ColetorDublado(impressoras, criar_resposta_snmp(), anteriores={}) as dublê:
            await executar_coleta(TriggerType.MANUAL)

        dublê.registrar_troca.assert_not_awaited()

    async def test_caixa_de_residuo_nao_gera_troca(self):
        # A caixa de resíduo ENCHE em vez de esvaziar, então todo
        # esvaziamento pareceria uma troca de toner.
        impressoras = [criar_impressora(1, "10.0.0.1")]
        anteriores = {1: criar_leitura_anterior(Decimal("5"))}

        with ColetorDublado(impressoras, criar_resposta_snmp(tipo="4"), anteriores) as d:
            await executar_coleta(TriggerType.MANUAL)

        d.registrar_troca.assert_not_awaited()

    async def test_toner_cartridge_do_parque_dispara_a_deteccao(self):
        # A Canon iR1643i II reporta o T06 como tonerCartridge (21), não
        # toner (3). Aceitando só o 3, nenhuma troca do parque era
        # detectada - foi o que a Fase 4B.4 encontrou.
        impressoras = [criar_impressora(1, "10.0.0.1")]
        anteriores = {1: criar_leitura_anterior(Decimal("5"))}

        with ColetorDublado(impressoras, criar_resposta_snmp(tipo="21"), anteriores) as d:
            await executar_coleta(TriggerType.MANUAL)

        d.registrar_troca.assert_awaited_once()

    async def test_leitura_sem_nivel_nao_gera_troca(self):
        # Um paralelo que volta a reportar nível criaria uma troca
        # fantasma se leituras sem medição entrassem na comparação.
        impressoras = [criar_impressora(1, "10.0.0.1")]
        anteriores = {1: criar_leitura_anterior(Decimal("5"))}

        with ColetorDublado(impressoras, criar_resposta_snmp(nivel="-2"), anteriores) as d:
            await executar_coleta(TriggerType.MANUAL)

        d.registrar_troca.assert_not_awaited()


class TestIdentificacaoDaImpressora:
    async def test_marca_e_modelo_sao_gravados(self):
        impressoras = [criar_impressora(1, "10.0.0.1")]
        with ColetorDublado(impressoras, criar_resposta_snmp()) as dublê:
            await executar_coleta(TriggerType.MANUAL)

        dublê.registrar_identificacao.assert_awaited_once_with(1, "Canon", "iR-ADV 4525")


class TestColetaSobreposta:
    async def test_excecao_e_propagada_para_quem_chamou(self):
        # O collector não decide o que fazer: o cli.py avisa o operador e
        # o agendador registra que pulou a rodada.
        impressoras = [criar_impressora(1, "10.0.0.1")]
        with ColetorDublado(impressoras, criar_resposta_snmp()) as dublê:
            dublê.iniciar.side_effect = ColetaEmAndamento("já rodando")

            with pytest.raises(ColetaEmAndamento):
                await executar_coleta(TriggerType.SCHEDULED)

        dublê.consultar.assert_not_awaited()
