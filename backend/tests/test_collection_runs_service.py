"""
Testes da decisão de status de uma execução de coleta.

A regra distingue "algumas impressoras não responderam" (PARTIAL, o dia
a dia normal) de "nenhuma respondeu" (FAILED, que sugere problema de
rede e merece investigação).
"""

from collection_runs.entities import CollectionStatus
from collection_runs.service import _decidir_status


class TestDecidirStatus:
    def test_todas_responderam(self):
        assert _decidir_status(total=66, sucesso=66) is CollectionStatus.COMPLETED

    def test_algumas_responderam(self):
        assert _decidir_status(total=66, sucesso=60) is CollectionStatus.PARTIAL

    def test_nenhuma_respondeu_sugere_problema_geral(self):
        assert _decidir_status(total=66, sucesso=0) is CollectionStatus.FAILED

    def test_nada_foi_tentado(self):
        # Falhou antes de conseguir buscar a lista de impressoras ativas.
        assert _decidir_status(total=0, sucesso=0) is CollectionStatus.FAILED

    def test_uma_unica_impressora_que_respondeu(self):
        assert _decidir_status(total=1, sucesso=1) is CollectionStatus.COMPLETED
