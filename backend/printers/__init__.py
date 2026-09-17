"""
Domínio: Impressoras.

Reúne o cadastro das impressoras monitoradas (tabela `impressora`):
entidade, acesso a dado (repository) e regra de negócio (service).

Uso recomendado por outros domínios/módulos:
    from printers.service import listar_impressoras_ativas
"""

from printers.entities import Impressora

__all__ = ["Impressora"]
