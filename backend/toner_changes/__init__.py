"""
Domínio: Trocas de Toner.

Reúne o registro dos eventos de substituição de cartucho (tabela
`troca_toner`): entidade, acesso a dado (repository) e regra de
negócio (service) - inclusive a decisão do que caracteriza uma troca
e o cálculo de quantas páginas o cartucho anterior rendeu.

Uso recomendado por outros domínios/módulos:
    from toner_changes.service import registrar_se_houve_troca
"""

from toner_changes.entities import TrocaToner

__all__ = ["TrocaToner"]
