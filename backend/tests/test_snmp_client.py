"""
Testes do cliente SNMP que não dependem da rede.

- Dedução de marca a partir do sysDescr: texto livre, cada fabricante
  escreve do seu jeito, então a função é uma heurística - estes testes
  fixam o comportamento esperado para os casos que o parque produz.
- Qual consulta cada contador de página usa (get ou walk), e o que
  acontece quando parte das consultas volta em branco, com a rede
  substituída por dublês.
"""

from dataclasses import replace
from unittest.mock import patch

from collector import oids
from collector.snmp_client import _ResultadoWalk, consultar_impressora, deduzir_marca
from config import settings


class TestDeduzirMarca:
    def test_canon_no_inicio(self):
        assert deduzir_marca("Canon iR-ADV 4525") == "Canon"

    def test_marca_no_meio_do_texto(self):
        # O sysDescr nem sempre começa pelo nome do fabricante.
        assert deduzir_marca("imageRUNNER ADVANCE, Canon Inc.") == "Canon"

    def test_case_insensitive(self):
        assert deduzir_marca("CANON LBP6030") == "Canon"

    def test_hewlett_packard_normaliza_para_hp(self):
        assert deduzir_marca("Hewlett-Packard LaserJet") == "HP"

    def test_fabricante_desconhecido_usa_primeiro_token(self):
        # Mantém a função útil para um fabricante ainda não mapeado, em
        # vez de devolver nada.
        assert deduzir_marca("Zebra ZT411 print server") == "Zebra"

    def test_sem_sys_descr(self):
        assert deduzir_marca(None) is None
        assert deduzir_marca("") is None


OID_TOTAL_CANON = "1.3.6.1.4.1.1602.1.11.1.3.1.4.101"
OID_COPIAS_CANON = "1.3.6.1.4.1.1602.1.11.1.3.1.4.201"


SYS_DESCR_CANON = "Canon iR1643i II /P"


async def consultar_com_rede_dublada(
    oid_total=None, oid_copias=None, falhas=None, vazios=()
):
    """
    Roda consultar_impressora com walk e get substituídos por dublês.

    `falhas` mapeia OID -> quantas vezes seguidas ele volta em branco
    (vazio COM erro, como num timeout) antes de responder. `vazios` são
    OIDs que o agente não implementa (vazio SEM erro).

    Devolve (coleta, OIDs consultados por walk, OIDs consultados por get),
    com repetição, na ordem das chamadas.
    """
    falhas = dict(falhas or {})
    walks, gets = [], []
    valores_walk = {
        oids.OID_MARKER_LIFE_COUNT: {"1.1": "2913"},
        oids.OID_SYS_DESCR: {"0": SYS_DESCR_CANON},
        oids.OID_SUPPLIES_LEVEL: {"1.1": "80"},
        oids.OID_SUPPLIES_MAX_CAPACITY: {"1.1": "100"},
    }
    valores_get = {OID_TOTAL_CANON: "2926", OID_COPIAS_CANON: "42"}

    def responder(oid, valores):
        if falhas.get(oid, 0) > 0:
            falhas[oid] -= 1
            return _ResultadoWalk(valores={}, erro="No SNMP response received before timeout")
        if oid in vazios:
            return _ResultadoWalk(valores={})
        return _ResultadoWalk(valores=valores)

    async def walk(ip, community, base_oid, *args):
        walks.append(base_oid)
        return responder(base_oid, valores_walk.get(base_oid, {"1": "x"}))

    async def get(ip, community, oid, *args):
        gets.append(oid)
        return responder(oid, {"": valores_get[oid]})

    snmp = replace(
        settings.snmp, oid_contador_total=oid_total, oid_contador_copias=oid_copias
    )
    with (
        patch("collector.snmp_client._walk_oid", side_effect=walk),
        patch("collector.snmp_client._get_oid", side_effect=get),
        patch("collector.snmp_client.settings", replace(settings, snmp=snmp)),
    ):
        coleta = await consultar_impressora("10.0.0.1")
    return coleta, walks, gets


class TestContadoresDePagina:
    """
    Os OIDs do .env são configurados completos, com o índice da linha.
    Um walk a partir de um OID folha devolve o que vem DEPOIS dele, e o
    contador saía sempre None - foi o que a Fase 4B.3 encontrou. Estes
    testes fixam que OID configurado é lido por get, e só a tabela
    padrão prtMarkerLifeCount continua por walk.
    """

    async def _consultar(self, oid_total, oid_copias):
        return await consultar_com_rede_dublada(oid_total, oid_copias)

    async def test_oids_configurados_sao_lidos_por_get(self):
        coleta, walks, gets = await self._consultar(OID_TOTAL_CANON, OID_COPIAS_CANON)
        assert coleta.paginas_total == 2926
        assert coleta.paginas_copias == 42
        assert sorted(gets) == [OID_TOTAL_CANON, OID_COPIAS_CANON]
        assert OID_TOTAL_CANON not in walks and OID_COPIAS_CANON not in walks

    async def test_total_configurado_nao_recai_no_padrao(self):
        # Misturar as duas fontes entre coletas distorceria paginas_rendidas.
        _, walks, _ = await self._consultar(OID_TOTAL_CANON, None)
        assert oids.OID_MARKER_LIFE_COUNT not in walks

    async def test_sem_configuracao_usa_prt_marker_life_count(self):
        coleta, walks, gets = await self._consultar(None, None)
        assert coleta.paginas_total == 2913
        assert coleta.paginas_copias is None
        assert oids.OID_MARKER_LIFE_COUNT in walks
        assert gets == []


class TestRespostaParcial:
    """
    Impressora que responde a algumas consultas e não a outras - como a
    10.165.20.82 na coleta #1, gravada sem marca porque o sysDescr não
    chegou. O que voltou em branco é repetido uma vez; persistindo,
    consulta essencial derruba a impressora para falha em vez de gravar
    leitura errada, e não essencial vira None registrado.
    """

    async def test_consulta_em_branco_e_repetida(self):
        coleta, walks, _ = await consultar_com_rede_dublada(
            falhas={oids.OID_SYS_DESCR: 1}
        )
        assert coleta.respondeu
        assert coleta.marca == "Canon"
        assert coleta.consultas_em_branco == []
        assert walks.count(oids.OID_SYS_DESCR) == 2

    async def test_so_repete_o_que_falhou(self):
        _, walks, _ = await consultar_com_rede_dublada(falhas={oids.OID_SYS_DESCR: 1})
        assert walks.count(oids.OID_SUPPLIES_LEVEL) == 1

    async def test_nao_essencial_em_branco_segue_e_fica_registrada(self):
        coleta, _, _ = await consultar_com_rede_dublada(
            falhas={oids.OID_SYS_DESCR: 2}
        )
        assert coleta.respondeu
        assert coleta.marca is None
        assert coleta.niveis == {"1.1": "80"}
        assert coleta.consultas_em_branco == ["sys_descr"]

    async def test_nivel_em_branco_derruba_para_falha(self):
        # Sem nível, a impressora seria gravada como sucesso sem nenhum
        # suprimento; sem capacidade, com toda linha em status 'erro'.
        coleta, _, _ = await consultar_com_rede_dublada(
            falhas={oids.OID_SUPPLIES_LEVEL: 2}
        )
        assert not coleta.respondeu
        assert "niveis" in coleta.erro

    async def test_capacidade_em_branco_derruba_para_falha(self):
        coleta, _, _ = await consultar_com_rede_dublada(
            falhas={oids.OID_SUPPLIES_MAX_CAPACITY: 2}
        )
        assert not coleta.respondeu
        assert "capacidades" in coleta.erro

    async def test_contador_em_branco_derruba_para_falha(self):
        # Uma troca detectada numa leitura sem contador perderia o
        # rendimento deste cartucho e do próximo.
        coleta, _, _ = await consultar_com_rede_dublada(
            OID_TOTAL_CANON, OID_COPIAS_CANON, falhas={OID_COPIAS_CANON: 2}
        )
        assert not coleta.respondeu
        assert "paginas_copias" in coleta.erro

    async def test_oid_nao_implementado_nao_e_falha(self):
        # Vazio sem erro é o agente dizendo que não tem o OID: não repete
        # nem registra como resposta parcial.
        coleta, walks, _ = await consultar_com_rede_dublada(
            vazios={oids.OID_PRINTER_NAME}
        )
        assert coleta.respondeu
        assert coleta.consultas_em_branco == []
        assert walks.count(oids.OID_PRINTER_NAME) == 1
