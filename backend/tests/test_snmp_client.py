"""
Testes da dedução de marca a partir do sysDescr.

O sysDescr é texto livre e cada fabricante escreve do seu jeito, então
a função é uma heurística - estes testes fixam o comportamento
esperado dela para os casos que o parque produz.
"""

from collector.snmp_client import deduzir_marca


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
