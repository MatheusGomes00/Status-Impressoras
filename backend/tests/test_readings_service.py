"""
Testes da interpretação das leituras SNMP.

Todas as funções cobertas aqui são puras: recebem os valores crus como
strings e devolvem uma LeituraPendente. Não tocam banco nem rede, o que
é o ponto - são a regra mais sujeita a erro do projeto e a que mais
precisa continuar valendo quando alguém mexer no coletor.
"""

from decimal import Decimal

from readings.entities import ReadingStatus
from readings.service import (
    calcular_percentual,
    indice_do_sufixo,
    interpretar_leitura,
    montar_leitura_sem_resposta,
    montar_leituras_snmp,
    nivel_e_mensuravel,
)


class TestCalcularPercentual:
    def test_calculo_simples(self):
        assert calcular_percentual(850, 1000) == Decimal("85.0")

    def test_arredonda_para_uma_casa(self):
        assert calcular_percentual(1, 3) == Decimal("33.3")

    def test_nivel_negativo_nao_e_mensuravel(self):
        assert calcular_percentual(-3, 100) is None

    def test_capacidade_zero_nao_e_mensuravel(self):
        assert calcular_percentual(50, 0) is None


class TestIndiceDoSufixo:
    def test_usa_o_supply_index_e_nao_a_posicao(self):
        # O sufixo é "<hrDeviceIndex>.<supplyIndex>"; o que importa é o último.
        assert indice_do_sufixo("1.2") == 2

    def test_sufixo_com_um_componente(self):
        assert indice_do_sufixo("1") == 1

    def test_indice_de_dois_digitos(self):
        assert indice_do_sufixo("1.10") == 10

    def test_sufixo_invalido_cai_para_um(self):
        assert indice_do_sufixo("abc") == 1


class TestTonerParalelo:
    """
    Cartucho paralelo não reporta nível. Precisa virar 'nao_reportado',
    nunca 'erro': o consumível não informar não é falha do sistema, e
    misturar os dois faria a taxa de falha da coleta medir qualidade de
    cartucho em vez de saúde da rede.
    """

    def test_valores_vazios_ou_marcadores(self):
        for bruto in ["", "   ", "*", "-", "--", "-%", "N/D", "n/a", None]:
            leitura = interpretar_leitura(1, bruto, "1000")
            assert leitura.status is ReadingStatus.NAO_REPORTADO, bruto

    def test_oid_nao_implementado(self):
        leitura = interpretar_leitura(
            1, "No Such Object currently exists at this OID", "1000"
        )
        assert leitura.status is ReadingStatus.NAO_REPORTADO

    def test_codigo_especial_da_rfc_3805(self):
        # -2 (desconhecido) é como o paralelo costuma responder, e é o que
        # a interface da impressora exibe como "-%".
        for codigo in ["-1", "-2", "-3"]:
            leitura = interpretar_leitura(1, codigo, "1000")
            assert leitura.status is ReadingStatus.NAO_REPORTADO, codigo
            assert leitura.nivel_percentual is None

    def test_capacidade_desconhecida(self):
        leitura = interpretar_leitura(1, "500", "-2")
        assert leitura.status is ReadingStatus.NAO_REPORTADO
        assert leitura.nivel_percentual is None

    def test_valor_cru_e_preservado_para_diagnostico(self):
        # Nada se perde: o código especial continua gravado.
        leitura = interpretar_leitura(1, "-2", "1000")
        assert leitura.nivel_bruto == -2
        assert leitura.capacidade_max == 1000


class TestInterpretarLeitura:
    def test_leitura_normal(self):
        leitura = interpretar_leitura(1, "850", "1000")
        assert leitura.status is ReadingStatus.OK
        assert leitura.nivel_percentual == Decimal("85.0")

    def test_valor_realmente_inesperado_e_erro(self):
        leitura = interpretar_leitura(1, "abc123xyz", "1000")
        assert leitura.status is ReadingStatus.ERRO

    def test_indice_sem_capacidade_correspondente_e_erro(self):
        # MIB inconsistente: o suprimento existe na tabela de níveis mas
        # não na de capacidades. Antes sumia; agora fica visível.
        leitura = interpretar_leitura(1, "850", None)
        assert leitura.status is ReadingStatus.ERRO
        assert leitura.nivel_bruto == 850
        assert leitura.capacidade_max is None

    def test_nivel_e_mensuravel(self):
        assert nivel_e_mensuravel(850, 1000)
        assert not nivel_e_mensuravel(-2, 1000)
        assert not nivel_e_mensuravel(850, 0)


class TestMontarLeiturasSnmp:
    """
    O pareamento das tabelas da prtMarkerSupplies é pela chave da linha
    no OID, não pela posição. Estes testes existem para impedir que
    alguém volte ao zip() do MVP.
    """

    def test_casa_pelo_indice_do_oid(self):
        leituras = montar_leituras_snmp(
            niveis={"1.1": "850", "1.2": "40"},
            capacidades={"1.1": "1000", "1.2": "100"},
        )
        por_indice = {l.indice_suprimento: l for l in leituras}
        assert por_indice[1].nivel_percentual == Decimal("85.0")
        assert por_indice[2].nivel_percentual == Decimal("40.0")

    def test_ordem_de_chegada_nao_importa(self):
        leituras = montar_leituras_snmp(
            niveis={"1.2": "40", "1.1": "850"},
            capacidades={"1.1": "1000", "1.2": "100"},
        )
        por_indice = {l.indice_suprimento: l for l in leituras}
        assert por_indice[1].nivel_percentual == Decimal("85.0")

    def test_nenhum_suprimento_some_quando_falta_par(self):
        # Com zip(), o suprimento sem par desaparecia silenciosamente e o
        # outro era dividido pela capacidade errada.
        leituras = montar_leituras_snmp(
            niveis={"1.1": "850", "1.2": "40"},
            capacidades={"1.2": "100"},
        )
        assert len(leituras) == 2
        por_indice = {l.indice_suprimento: l for l in leituras}
        assert por_indice[1].status is ReadingStatus.ERRO
        assert por_indice[1].nivel_percentual is None

    def test_descricao_e_tipo_casam_com_o_mesmo_suprimento(self):
        leituras = montar_leituras_snmp(
            niveis={"1.1": "850", "1.2": "10"},
            capacidades={"1.1": "1000", "1.2": "100"},
            descricoes={"1.1": "Black Toner", "1.2": "Waste Toner Box"},
            tipos={"1.1": "3", "1.2": "4"},
        )
        por_indice = {l.indice_suprimento: l for l in leituras}
        assert por_indice[1].descricao_suprimento == "Black Toner"
        assert por_indice[1].tipo_suprimento == 3
        assert por_indice[2].tipo_suprimento == 4

    def test_contadores_vao_para_todas_as_linhas_da_coleta(self):
        leituras = montar_leituras_snmp(
            niveis={"1.1": "850", "1.2": "10"},
            capacidades={"1.1": "1000", "1.2": "100"},
            paginas_total=48210,
            paginas_copias=12000,
        )
        assert all(l.paginas_total == 48210 for l in leituras)
        assert all(l.paginas_copias == 12000 for l in leituras)

    def test_sem_suprimentos_devolve_lista_vazia(self):
        assert montar_leituras_snmp({}, {}) == []


class TestSemResposta:
    def test_status_e_indice(self):
        leitura = montar_leitura_sem_resposta()
        assert leitura.status is ReadingStatus.SEM_RESPOSTA
        assert leitura.indice_suprimento == 1
        assert leitura.nivel_percentual is None
