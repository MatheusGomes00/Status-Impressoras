"""
Testes do cliente SNMP que não dependem da rede.

- Dedução de marca a partir do sysDescr: texto livre, cada fabricante
  escreve do seu jeito, então a função é uma heurística - estes testes
  fixam o comportamento esperado para os casos que o parque produz.
- Qual consulta cada contador de página usa (get ou walk), com a rede
  substituída por dublês.
"""

from dataclasses import replace
from unittest.mock import AsyncMock, patch

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


class TestContadoresDePagina:
    """
    Os OIDs do .env são configurados completos, com o índice da linha.
    Um walk a partir de um OID folha devolve o que vem DEPOIS dele, e o
    contador saía sempre None - foi o que a Fase 4B.3 encontrou. Estes
    testes fixam que OID configurado é lido por get, e só a tabela
    padrão prtMarkerLifeCount continua por walk.
    """

    async def _consultar(self, oid_total, oid_copias):
        walks = []

        async def walk(ip, community, base_oid, *args):
            walks.append(base_oid)
            valores = {"1.1": "2913"} if base_oid == oids.OID_MARKER_LIFE_COUNT else {"1": "x"}
            return _ResultadoWalk(valores=valores)

        valores_get = {OID_TOTAL_CANON: "2926", OID_COPIAS_CANON: "42"}
        get = AsyncMock(
            side_effect=lambda ip, community, oid, *args: _ResultadoWalk(
                valores={"": valores_get[oid]}
            )
        )
        snmp = replace(
            settings.snmp, oid_contador_total=oid_total, oid_contador_copias=oid_copias
        )
        with (
            patch("collector.snmp_client._walk_oid", side_effect=walk),
            patch("collector.snmp_client._get_oid", get),
            patch("collector.snmp_client.settings", replace(settings, snmp=snmp)),
        ):
            coleta = await consultar_impressora("10.0.0.1")
        return coleta, walks, [c.args[2] for c in get.call_args_list]

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
