"""
Coletor SNMP.

Reúne o cliente SNMP (snmp_client.py), os OIDs consultados (oids.py) e
a orquestração de uma execução de coleta inteira (service.py).

Diferente dos outros pacotes, este não é um domínio com tabela própria:
é o processo que alimenta os domínios `readings`, `collection_runs` e
`toner_changes` a partir do que as impressoras respondem.

Uso recomendado:
    from collector.service import executar_coleta
"""

from collector.snmp_client import ColetaSnmp, consultar_impressora

__all__ = ["ColetaSnmp", "consultar_impressora"]
