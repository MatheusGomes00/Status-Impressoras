"""
Testes da detecção de troca de cartucho.

O que está sendo protegido aqui é o número que hoje é anotado à mão na
caixa do toner: um falso positivo inventa um cartucho que ninguém
trocou, e um falso negativo some com o rendimento de um cartucho real.
"""

from decimal import Decimal

from toner_changes.service import _rendimento, houve_troca


class TestHouveTroca:
    def test_salto_grande_e_troca(self):
        assert houve_troca(Decimal("5"), Decimal("95"))

    def test_nivel_caindo_nao_e_troca(self):
        assert not houve_troca(Decimal("85"), Decimal("83"))

    def test_oscilacao_pequena_nao_e_troca(self):
        # O nível reportado sobe alguns pontos sozinho (medição por
        # estimativa). Sem o limiar, isso viraria dezenas de trocas
        # fantasma por mês.
        assert not houve_troca(Decimal("85"), Decimal("90"))

    def test_exatamente_no_limiar_e_troca(self):
        assert houve_troca(Decimal("10"), Decimal("30"))

    def test_primeira_leitura_nunca_e_troca(self):
        # Sem nível anterior não dá para afirmar nada - senão toda
        # impressora nova geraria um evento falso na primeira coleta.
        assert not houve_troca(None, Decimal("100"))

    def test_nivel_atual_desconhecido_nao_e_troca(self):
        assert not houve_troca(Decimal("50"), None)


class TestRendimento:
    def test_diferenca_entre_contadores(self):
        assert _rendimento(50000, 2000) == 48000

    def test_primeira_troca_nao_tem_marco_anterior(self):
        assert _rendimento(50000, None) is None

    def test_sem_contador_atual(self):
        assert _rendimento(None, 2000) is None

    def test_contador_que_regrediu_vira_none(self):
        # Reset de contador na impressora. Um rendimento negativo no
        # relatório seria pior que a ausência do dado.
        assert _rendimento(100, 5000) is None

    def test_contador_identico_rende_zero(self):
        assert _rendimento(5000, 5000) == 0
