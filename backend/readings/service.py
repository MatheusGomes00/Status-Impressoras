"""
Service do domínio Leituras de Toner.

Aqui mora a lógica de interpretar os valores brutos devolvidos pelo
SNMP e transformá-los em leituras prontas para persistir - incluindo
os códigos especiais da RFC 3805 (nível negativo = suprimento não
medível, ex: caixa de resíduo) que já tratamos no MVP original
(teste_snmp.py).

O repository não sabe nada disso: ele só grava o que este arquivo
decidir que deve ser gravado.

MUDANÇA EM RELAÇÃO AO MVP - pareamento por índice do OID
--------------------------------------------------------
O MVP casava níveis e capacidades por POSIÇÃO (`zip(niveis, capacidades)`).
Isso só está correto se as tabelas prtMarkerSuppliesLevel (.9) e
prtMarkerSuppliesMaxCapacity (.8) devolverem exatamente os mesmos
índices, na mesma ordem e na mesma quantidade.

A Canon expõe na prtMarkerSupplies não só o toner, mas também a caixa
de resíduo e às vezes o tambor - e é comum uma tabela ter uma linha que
a outra não tem. Quando isso acontece, o nível do toner acaba dividido
pela capacidade da caixa de resíduo: sai um percentual plausível, porém
errado. Era a causa provável do valor divergir do painel da impressora.
Além disso, zip() trunca em silêncio: 3 níveis com 2 capacidades faziam
um suprimento sumir sem nenhum erro.

Por isso o casamento agora é feito pela CHAVE DA LINHA na MIB (o sufixo
do OID), e não pela ordem de chegada. O cálculo do percentual e o
tratamento dos códigos da RFC 3805 seguem idênticos ao MVP.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from readings import repository
from readings.entities import LeituraToner, ReadingStatus

# Valores que impressoras com cartucho paralelo devolvem no lugar do
# nível. Não são falha do sistema - o consumível simplesmente não
# reporta medição -, por isso viram NAO_REPORTADO e não ERRO.
_MARCADORES_SEM_MEDICAO = frozenset(
    {"", "*", "-", "--", "-%", "n/d", "n/a", "na", "null", "none", "?"}
)


@dataclass
class LeituraPendente:
    """
    Representa uma leitura já interpretada, mas ainda não associada a
    uma impressora/coleta específicas - essa associação acontece no
    collector/service.py, que é quem sabe qual impressora e qual
    execução de coleta estão em andamento.
    """
    indice_suprimento: int
    descricao_suprimento: str | None
    tipo_suprimento: int | None
    nivel_bruto: int | None
    capacidade_max: int | None
    nivel_percentual: Decimal | None
    paginas_total: int | None
    paginas_copias: int | None
    status: ReadingStatus


def calcular_percentual(nivel_bruto: int, capacidade_max: int) -> Decimal | None:
    """
    Calcula o percentual de um suprimento.

    Retorna None (não é erro) quando o valor não é medível segundo a
    RFC 3805: nível negativo (códigos especiais como -2/-3) ou
    capacidade máxima <= 0. Isso é comum em suprimentos como caixa de
    resíduo, que alguns modelos não reportam em percentual.
    """
    if nivel_bruto < 0 or capacidade_max <= 0:
        return None

    percentual = (Decimal(nivel_bruto) / Decimal(capacidade_max)) * 100
    return percentual.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def indice_do_sufixo(sufixo: str) -> int:
    """
    Converte o sufixo do OID de uma linha da prtMarkerSupplies no índice
    do suprimento que vai para o banco.

    O sufixo vem no formato "<hrDeviceIndex>.<supplyIndex>" (ex: "1.2").
    Guardamos o supplyIndex, que é estável ao longo do tempo para a mesma
    impressora - ao contrário da posição na resposta, que muda se uma
    linha deixar de ser reportada e quebraria a continuidade do histórico.
    """
    ultimo = sufixo.rsplit(".", 1)[-1]
    try:
        return int(ultimo)
    except ValueError:
        return 1


def _classificar_nao_numerico(bruto: str | None) -> ReadingStatus:
    """
    Decide se um valor que não converteu para int é cartucho paralelo
    (não reporta) ou resposta realmente inesperada (erro).
    """
    if bruto is None:
        return ReadingStatus.NAO_REPORTADO

    texto = bruto.strip().lower()
    if texto in _MARCADORES_SEM_MEDICAO:
        return ReadingStatus.NAO_REPORTADO
    # pysnmp devolve textos como "No Such Object currently exists at this OID"
    # quando o agente não implementa o OID - isso é ausência de medição,
    # não formato corrompido.
    if "no such" in texto:
        return ReadingStatus.NAO_REPORTADO

    return ReadingStatus.ERRO


def _para_int(bruto: str | None) -> int | None:
    """Converte um valor bruto do SNMP em int, ou None se não for numérico."""
    if bruto is None:
        return None
    try:
        return int(bruto.strip())
    except (AttributeError, ValueError):
        return None


def nivel_e_mensuravel(nivel_bruto: int, capacidade_max: int) -> bool:
    """
    True quando o par (nível, capacidade) permite calcular um percentual.

    É falso nos códigos especiais da RFC 3805 - nível negativo (-1 outro,
    -2 desconhecido, -3 "resta alguma coisa") ou capacidade <= 0. Esses
    códigos são a forma mais comum de um cartucho paralelo dizer que não
    sabe informar o nível.
    """
    return nivel_bruto >= 0 and capacidade_max > 0


def interpretar_leitura(
    indice_suprimento: int,
    nivel_bruto_str: str | None,
    capacidade_max_str: str | None,
    descricao_suprimento: str | None = None,
    tipo_suprimento_str: str | None = None,
    paginas_total: int | None = None,
    paginas_copias: int | None = None,
) -> LeituraPendente:
    """
    Converte os valores brutos (strings, como vêm do SNMP) em uma
    LeituraPendente, decidindo o status:
      - 'ok'            -> o nível foi lido e é um percentual de verdade
      - 'nao_reportado' -> a impressora respondeu, mas não informou nível:
                           valor vazio/'*'/'-%', OID não implementado, ou
                           código especial da RFC 3805 (nível negativo,
                           capacidade <= 0)
      - 'erro'          -> resposta em formato realmente inesperado, ou o
                           índice existe na tabela de níveis mas não na de
                           capacidades (MIB inconsistente)

    'sem_resposta' não é decidido aqui - só existe quando a impressora
    inteira não respondeu à consulta SNMP (ver montar_leitura_sem_resposta).

    POR QUE código especial da RFC 3805 é 'nao_reportado' e não 'ok'
    ---------------------------------------------------------------
    Um cartucho paralelo costuma responder o código -2 (desconhecido) ou
    -3 no lugar do nível - é isso que a interface da impressora acaba
    exibindo como "-%". Classificar esse caso como 'ok' com
    nivel_percentual NULL deixava o paralelo indistinguível de um
    suprimento que legitimamente não é medível, e fazia o relatório de
    "onde há paralelo" não encontrar nada.

    Os valores crus continuam gravados (nivel_bruto guarda o -2), então
    nada se perde: o código especial segue disponível para diagnóstico.
    """
    nivel_bruto = _para_int(nivel_bruto_str)
    capacidade_max = _para_int(capacidade_max_str)
    tipo_suprimento = _para_int(tipo_suprimento_str)

    if nivel_bruto is None:
        return LeituraPendente(
            indice_suprimento=indice_suprimento,
            descricao_suprimento=descricao_suprimento,
            tipo_suprimento=tipo_suprimento,
            nivel_bruto=None,
            capacidade_max=capacidade_max,
            nivel_percentual=None,
            paginas_total=paginas_total,
            paginas_copias=paginas_copias,
            status=_classificar_nao_numerico(nivel_bruto_str),
        )

    if capacidade_max is None:
        # O suprimento aparece na tabela de níveis mas não na de
        # capacidades. No MVP isso sumia (zip truncava) ou, pior, era
        # pareado com a capacidade do suprimento errado. Agora fica
        # registrado e visível.
        return LeituraPendente(
            indice_suprimento=indice_suprimento,
            descricao_suprimento=descricao_suprimento,
            tipo_suprimento=tipo_suprimento,
            nivel_bruto=nivel_bruto,
            capacidade_max=None,
            nivel_percentual=None,
            paginas_total=paginas_total,
            paginas_copias=paginas_copias,
            status=ReadingStatus.ERRO,
        )

    mensuravel = nivel_e_mensuravel(nivel_bruto, capacidade_max)

    return LeituraPendente(
        indice_suprimento=indice_suprimento,
        descricao_suprimento=descricao_suprimento,
        tipo_suprimento=tipo_suprimento,
        nivel_bruto=nivel_bruto,
        capacidade_max=capacidade_max,
        nivel_percentual=(
            calcular_percentual(nivel_bruto, capacidade_max) if mensuravel else None
        ),
        paginas_total=paginas_total,
        paginas_copias=paginas_copias,
        status=ReadingStatus.OK if mensuravel else ReadingStatus.NAO_REPORTADO,
    )


def montar_leituras_snmp(
    niveis: dict[str, str],
    capacidades: dict[str, str],
    descricoes: dict[str, str] | None = None,
    tipos: dict[str, str] | None = None,
    paginas_total: int | None = None,
    paginas_copias: int | None = None,
) -> list[LeituraPendente]:
    """
    Monta uma LeituraPendente por suprimento, casando as tabelas da
    prtMarkerSupplies pela chave da linha (sufixo do OID).

    Cada dicionário vem do collector no formato {sufixo_do_oid: valor},
    ex: {"1.1": "850", "1.2": "-2"}. O casamento por chave é o que
    garante que nível, capacidade e descrição sejam sempre do MESMO
    suprimento - ver a nota de mudança no topo deste módulo.
    """
    descricoes = descricoes or {}
    tipos = tipos or {}

    return [
        interpretar_leitura(
            indice_suprimento=indice_do_sufixo(sufixo),
            nivel_bruto_str=niveis[sufixo],
            capacidade_max_str=capacidades.get(sufixo),
            descricao_suprimento=descricoes.get(sufixo),
            tipo_suprimento_str=tipos.get(sufixo),
            paginas_total=paginas_total,
            paginas_copias=paginas_copias,
        )
        for sufixo in sorted(niveis, key=indice_do_sufixo)
    ]


def montar_leitura_sem_resposta() -> LeituraPendente:
    """
    Representa a leitura de uma impressora que não respondeu à consulta
    SNMP (timeout/erro de rede). Sempre índice 1, já que não há como
    saber quantos suprimentos ela teria sem uma resposta.
    """
    return LeituraPendente(
        indice_suprimento=1,
        descricao_suprimento=None,
        tipo_suprimento=None,
        nivel_bruto=None,
        capacidade_max=None,
        nivel_percentual=None,
        paginas_total=None,
        paginas_copias=None,
        status=ReadingStatus.SEM_RESPOSTA,
    )


async def salvar_leituras(
    id_impressora: int,
    id_coleta_executada: int,
    leituras: list[LeituraPendente],
) -> list[int]:
    """
    Persiste um lote de leituras já interpretadas para uma impressora/coleta.

    Devolve os ids gerados, na mesma ordem das leituras - o detector de
    troca de cartucho precisa deles para apontar qual leitura registrou
    o salto de nível.
    """
    return await repository.inserir_lote(id_impressora, id_coleta_executada, leituras)


async def historico_por_impressora(
    id_impressora: int,
    desde: datetime | None = None,
) -> list[LeituraToner]:
    """Retorna o histórico de leituras de uma impressora (usado pela futura API e pelo relatório)."""
    return await repository.listar_por_impressora(id_impressora, desde)


async def ultimas_leituras_por_impressora() -> dict[int, list[LeituraToner]]:
    """
    Retorna, para cada impressora, as leituras da sua execução de
    coleta mais recente (todas as cores, se for colorida) - base do
    endpoint GET /printers e do dashboard de status atual.
    """
    return await repository.buscar_ultimas_por_impressora()


async def leituras_anteriores_por_suprimento(
    id_impressora: int,
    id_coleta_executada: int,
) -> dict[int, LeituraToner]:
    """
    Retorna a última leitura de cada suprimento ANTES da coleta informada.

    É a base da detecção de troca de cartucho: o coletor compara o nível
    recém-lido com o nível anterior do mesmo suprimento.
    """
    return await repository.buscar_anteriores_por_suprimento(
        id_impressora, id_coleta_executada
    )
