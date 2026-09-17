"""
Entidade do domínio Impressoras.

Representa uma linha da tabela `impressora` como um objeto Python
tipado, em vez de trafegar tuplas/dicionários soltos entre as camadas
de repository, service e (futuramente) API.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Impressora:
    id_impressora: int
    patrimonio: str | None
    ip_address: str
    location: str
    numero_serie: str | None
    marca: str | None
    modelo: str | None
    active: bool
    criado_em: datetime
    atualizado_em: datetime

    @classmethod
    def from_row(cls, row: dict) -> "Impressora":
        """
        Constrói uma Impressora a partir de uma linha retornada pelo
        aiomysql (usando DictCursor - ver repository.py).

        `bool(row["active"])` é usado de propósito: o driver pode
        devolver a coluna BOOLEAN/TINYINT como int (0/1) em vez de
        True/False nativo, dependendo da configuração do MariaDB/driver.
        Convertendo explicitamente aqui, o resto do código sempre
        trabalha com um bool de verdade, sem se preocupar com isso.
        """
        return cls(
            id_impressora=row["id_impressora"],
            patrimonio=row["patrimonio"],
            ip_address=row["ip_address"],
            location=row["location"],
            numero_serie=row["numero_serie"],
            marca=row["marca"],
            modelo=row["modelo"],
            active=bool(row["active"]),
            criado_em=row["criado_em"],
            atualizado_em=row["atualizado_em"],
        )
