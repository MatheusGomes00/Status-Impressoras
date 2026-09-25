"""
Entidade do domínio Leituras de Toner.

Representa uma linha da tabela `leitura_toner`. Cada linha é a leitura
de UM suprimento (um toner específico) de uma impressora, numa
execução de coleta específica - impressoras coloridas geram várias
linhas por coleta (uma por cor), diferenciadas por `indice_suprimento`.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum


class ReadingStatus(str, Enum):
    """
    Espelha o ENUM('ok', 'nao_reportado', 'erro', 'sem_resposta').

    A distinção entre NAO_REPORTADO e ERRO é operacional, não cosmética:
    cartucho paralelo devolve nível vazio/'*'/'-%' e isso NÃO é falha do
    sistema - é uma característica do consumível. Contar isso como erro
    inflaria a taxa de falha da coleta e esconderia problemas reais de
    rede. Separado, ainda vira um relatório útil ("onde há paralelo").
    """
    OK = "ok"
    NAO_REPORTADO = "nao_reportado"
    ERRO = "erro"
    SEM_RESPOSTA = "sem_resposta"


class SupplyType(int, Enum):
    """
    Valores de prtMarkerSuppliesType (RFC 3805) que interessam ao projeto.

    É o que impede o relatório de somar a caixa de resíduo como se fosse
    toner: numa Canon mono o walk devolve mais de uma linha, e só o tipo
    diz qual delas é o cartucho.
    """
    OTHER = 1
    UNKNOWN = 2
    TONER = 3
    WASTE_TONER = 4
    OPC = 9
    TONER_CARTRIDGE = 21
    """
    tonerCartridge. É o que a Canon iR1643i II reporta para o T06 - o
    parque inteiro na Fase 4B.4, nenhuma linha com TONER (3).
    """


def eh_tipo_toner(tipo_suprimento: int | None) -> bool:
    """
    True quando o tipo indica um cartucho de toner.

    Quando a impressora não informa o tipo (None), assume que é toner:
    num parque mono com um consumível só, tratar o desconhecido como
    toner erra menos que descartá-lo do relatório.
    """
    if tipo_suprimento is None:
        return True
    return tipo_suprimento in (SupplyType.TONER, SupplyType.TONER_CARTRIDGE)


@dataclass
class LeituraToner:
    id_leitura: int
    id_impressora: int
    id_coleta_executada: int
    indice_suprimento: int
    descricao_suprimento: str | None
    tipo_suprimento: int | None
    nivel_percentual: Decimal | None
    nivel_bruto: int | None
    capacidade_max: int | None
    paginas_total: int | None
    paginas_copias: int | None
    status: ReadingStatus
    coletado_em: datetime

    @property
    def eh_toner(self) -> bool:
        """True quando a leitura é de um cartucho de toner."""
        return eh_tipo_toner(self.tipo_suprimento)

    @classmethod
    def from_row(cls, row: dict) -> "LeituraToner":
        """Constrói uma LeituraToner a partir de uma linha do aiomysql (DictCursor)."""
        return cls(
            id_leitura=row["id_leitura"],
            id_impressora=row["id_impressora"],
            id_coleta_executada=row["id_coleta_executada"],
            indice_suprimento=row["indice_suprimento"],
            descricao_suprimento=row["descricao_suprimento"],
            tipo_suprimento=row["tipo_suprimento"],
            nivel_percentual=row["nivel_percentual"],
            nivel_bruto=row["nivel_bruto"],
            capacidade_max=row["capacidade_max"],
            paginas_total=row["paginas_total"],
            paginas_copias=row["paginas_copias"],
            status=ReadingStatus(row["status"]),
            coletado_em=row["coletado_em"],
        )
