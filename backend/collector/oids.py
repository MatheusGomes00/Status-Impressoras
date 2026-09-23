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

# ============================================================
# Contadores de página
#
# São os dois números que hoje são anotados à mão na caixa do toner a
# cada troca: TOTAL e CÓPIAS. Fazem parte do contexto de toda coleta -
# cada leitura grava o valor do contador no instante em que foi lida, e
# é isso que permite, na troca seguinte, calcular quantas páginas o
# cartucho rendeu.
#
# Onde cada um é gravado:
#
#   leitura_toner.paginas_total    <- OID_MARKER_LIFE_COUNT (abaixo)
#   leitura_toner.paginas_copias   <- SNMP_OID_CONTADOR_COPIAS (.env)
#
#   troca_toner.paginas_no_evento  <- paginas_total congelado na troca
#   troca_toner.paginas_rendidas   <- diferença desde a troca anterior
#   troca_toner.copias_no_evento   <- paginas_copias congelado na troca
#   troca_toner.copias_rendidas    <- diferença desde a troca anterior
# ============================================================

OID_MARKER_LIFE_COUNT = "1.3.6.1.2.1.43.10.2.1.4"
"""
prtMarkerLifeCount - total de páginas marcadas desde a fabricação.

É o contador universal da Printer-MIB e alimenta `paginas_total`. Fica
fixo aqui, e não em configuração, justamente por ser padrão: vale para
qualquer fabricante, então não há o que ajustar por parque.
"""

# --- Contador de CÓPIAS ---
# A Printer-MIB NÃO separa cópia de impressão: só existe o total. O
# contador de cópias existe apenas na MIB privada do fabricante (na
# Canon, sob BASE_PRIVADA_CANON) e o OID varia por série do equipamento.
#
# Por isso ele NÃO é uma constante deste arquivo: mora na variável de
# ambiente SNMP_OID_CONTADOR_COPIAS, lida por config.py e consumida por
# snmp_client.py. Assim o valor descoberto em campo é preenchido no
# servidor, sem alterar código.
#
# Enquanto estiver vazio, `paginas_copias` fica NULL e o relatório
# trabalha só com o total. Chutar um OID privado seria pior: a
# impressora responderia um número plausível de outra coisa e o
# relatório passaria a mentir em silêncio.

BASE_PRIVADA_CANON = "1.3.6.1.4.1.1602"
"""Raiz da MIB privada Canon, ponto de partida de descobrir_oids_canon.py."""
