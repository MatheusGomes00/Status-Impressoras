"""
Domínio: Leituras de Toner.

Reúne o histórico de níveis de toner coletados via SNMP (tabela
`leitura_toner`): entidade, enum de status, acesso a dado (repository)
e regra de negócio (service) - incluindo a interpretação dos valores
brutos do SNMP (cálculo de percentual, tratamento dos códigos
especiais da RFC 3805).

Uso recomendado por outros domínios/módulos:
    from readings.service import montar_leituras_snmp, salvar_leituras
"""

from readings.entities import LeituraToner, ReadingStatus, SupplyType

__all__ = ["LeituraToner", "ReadingStatus", "SupplyType"]
