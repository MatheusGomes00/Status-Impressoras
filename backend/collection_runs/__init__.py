"""
Domínio: Execuções de Coleta.

Reúne o registro de cada rodada da rotina de coleta (tabela
`coleta_executada`): entidade, enums, acesso a dado (repository) e
regra de negócio (service) - inclusive a decisão de status final
(COMPLETED / PARTIAL / FAILED).

Uso recomendado por outros domínios/módulos:
    from collection_runs.service import iniciar_coleta, finalizar_coleta
"""

from collection_runs.entities import ColetaExecutada, CollectionStatus, TriggerType

__all__ = ["ColetaExecutada", "CollectionStatus", "TriggerType"]
