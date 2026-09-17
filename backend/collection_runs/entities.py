"""
Entidade do domínio Execuções de Coleta.

Representa uma linha da tabela `coleta_executada`. Os dois campos
ENUM do banco (trigger_type e status) viram Enums Python, para evitar
strings soltas ("RUNNING", "manual" com letra errada, etc.) espalhadas
pelo código - o Python (e o editor) acusam erro de digitação em tempo
de desenvolvimento, em vez de falhar silenciosamente em produção.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class TriggerType(str, Enum):
    """Espelha o ENUM('SCHEDULED', 'MANUAL') da coluna trigger_type."""
    SCHEDULED = "SCHEDULED"
    MANUAL = "MANUAL"


class CollectionStatus(str, Enum):
    """Espelha o ENUM('RUNNING', 'COMPLETED', 'PARTIAL', 'FAILED') da coluna status."""
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


@dataclass
class ColetaExecutada:
    id: int
    trigger_type: TriggerType
    iniciado_em: datetime
    finalizado_em: datetime | None
    status: CollectionStatus
    total_impressoras: int
    impressoras_sucesso: int
    impressora_falha: int
    error_summary: str | None

    @classmethod
    def from_row(cls, row: dict) -> "ColetaExecutada":
        """Constrói uma ColetaExecutada a partir de uma linha do aiomysql (DictCursor)."""
        return cls(
            id=row["id"],
            trigger_type=TriggerType(row["trigger_type"]),
            iniciado_em=row["iniciado_em"],
            finalizado_em=row["finalizado_em"],
            status=CollectionStatus(row["status"]),
            total_impressoras=row["total_impressoras"],
            impressoras_sucesso=row["impressoras_sucesso"],
            impressora_falha=row["impressora_falha"],
            error_summary=row["error_summary"],
        )
