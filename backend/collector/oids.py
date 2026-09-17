"""
OIDs usados pela coleta, reunidos num único lugar.

Ficam isolados aqui (e não espalhados pelo snmp_client) porque são o
ponto do projeto mais sujeito a mudar quando entrar um modelo ou
fabricante diferente no parque: trocar de hardware deveria mexer
neste arquivo e em mais nenhum.

Todos os OIDs abaixo, com exceção do bloco Canon, são da Printer-MIB
padrão (RFC 3805) e valem para qualquer impressora de rede.
"""

# --- Identificação da impressora ---
OID_SYS_DESCR = "1.3.6.1.2.1.1.1"
"""sysDescr - texto livre do fabricante. Base para deduzir marca/modelo."""

OID_PRINTER_NAME = "1.3.6.1.2.1.43.5.1.1.16"
"""prtGeneralPrinterName - nome do modelo, quando a impressora expõe."""

OID_SERIAL_NUMBER = "1.3.6.1.2.1.43.5.1.1.17"
"""
prtGeneralSerialNumber.

Consultado por walk, e não por get num índice fixo, porque muitas Canon
não respondem no índice esperado pela MIB padrão (comportamento já
observado no MVP - ver teste_snmp.py).
"""

# --- Suprimentos (tabela prtMarkerSupplies) ---
# As quatro tabelas abaixo são indexadas pela MESMA chave
# (hrDeviceIndex.supplyIndex). É esse sufixo compartilhado que permite
# casar nível, capacidade, descrição e tipo do mesmo suprimento sem
# depender da ordem em que o agente devolve as linhas.
OID_SUPPLIES_TYPE = "1.3.6.1.2.1.43.11.1.1.5"
"""prtMarkerSuppliesType - 3=toner, 4=caixa de resíduo, 9=cilindro."""

OID_SUPPLIES_DESCRIPTION = "1.3.6.1.2.1.43.11.1.1.6"
"""prtMarkerSuppliesDescription - texto do suprimento ("Black Toner", etc.)."""

OID_SUPPLIES_MAX_CAPACITY = "1.3.6.1.2.1.43.11.1.1.8"
"""prtMarkerSuppliesMaxCapacity - capacidade máxima, ou código negativo."""

OID_SUPPLIES_LEVEL = "1.3.6.1.2.1.43.11.1.1.9"
"""prtMarkerSuppliesLevel - nível atual, ou código negativo da RFC 3805."""

# --- Contadores de página ---
OID_MARKER_LIFE_COUNT = "1.3.6.1.2.1.43.10.2.1.4"
"""
prtMarkerLifeCount - total de páginas marcadas desde a fabricação.

É o contador universal e é o que alimenta `paginas_total`.
"""

# --- Contadores Canon (MIB privada, ramo 1.3.6.1.4.1.1602) ---
# A Printer-MIB padrão NÃO separa impressão de cópia: só existe o total
# (prtMarkerLifeCount). A Canon expõe os contadores por função em MIB
# privada, e os OIDs variam por série do equipamento.
#
# Deixado como None de propósito: chutar um OID privado devolve um
# número errado silenciosamente, o que é pior para o relatório do que
# não ter o dado. Preencher depois de rodar descobrir_oids_canon.py
# contra uma impressora real do parque.
OID_CANON_CONTADOR_COPIAS: str | None = None
"""Contador de cópias da Canon. Pendente de descoberta em hardware real."""

BASE_PRIVADA_CANON = "1.3.6.1.4.1.1602"
"""Raiz da MIB privada Canon, usada pelo script de descoberta."""
