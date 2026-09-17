"""
Entidade do domínio Trocas de Toner.

Representa uma linha da tabela `troca_toner`: o evento de substituição
de um cartucho, detectado automaticamente quando o nível de um
suprimento sobe entre duas leituras consecutivas.

`paginas_rendidas` é o número que hoje é anotado à mão na caixa do
toner - quantas páginas o cartucho anterior imprimiu entre a troca
passada e esta.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass
class TrocaToner:
    id_troca: int
    id_impressora: int
    indice_suprimento: int
    id_leitura_antes: int | None
    id_leitura_depois: int
    detectado_em: datetime
    nivel_antes: Decimal | None
    nivel_depois: Decimal | None
    paginas_no_evento: int | None
    paginas_rendidas: int | None
    copias_no_evento: int | None
    copias_rendidas: int | None

    @classmethod
    def from_row(cls, row: dict) -> "TrocaToner":
        """Constrói uma TrocaToner a partir de uma linha do aiomysql (DictCursor)."""
        return cls(
            id_troca=row["id_troca"],
            id_impressora=row["id_impressora"],
            indice_suprimento=row["indice_suprimento"],
            id_leitura_antes=row["id_leitura_antes"],
            id_leitura_depois=row["id_leitura_depois"],
            detectado_em=row["detectado_em"],
            nivel_antes=row["nivel_antes"],
            nivel_depois=row["nivel_depois"],
            paginas_no_evento=row["paginas_no_evento"],
            paginas_rendidas=row["paginas_rendidas"],
            copias_no_evento=row["copias_no_evento"],
            copias_rendidas=row["copias_rendidas"],
        )
