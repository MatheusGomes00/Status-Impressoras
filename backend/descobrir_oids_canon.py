"""
Script de descoberta dos contadores de página da Canon.

Por que ele existe
------------------
A Printer-MIB padrão (RFC 3805) NÃO separa impressão de cópia: ela só
expõe o total, via prtMarkerLifeCount. Mas o que vocês anotam à mão na
caixa do toner são DOIS números - total e cópias -, e o contador de
cópias só existe na MIB privada da Canon, cujos OIDs variam por série
do equipamento.

Chutar um OID privado é pior que não ter o dado: a impressora responde
um número plausível de outra coisa e o relatório passa a mentir em
silêncio. Por isso SNMP_OID_CONTADOR_COPIAS nasce vazio no .env e só é
preenchido depois de confirmado contra uma impressora real.

Como usar
---------
    python descobrir_oids_canon.py --ip 10.165.20.133

Rode com a impressora à mão. Anote no painel os contadores de TOTAL e
de CÓPIAS e compare com a saída: o OID cujo valor bater com o painel é
o que deve ser preenchido em OID_CANON_CONTADOR_COPIAS.

Para varrer só um ramo específico:
    python descobrir_oids_canon.py --ip 10.165.20.133 --oid 1.3.6.1.4.1.1602.1.11.1.3

ATENÇÃO: o walk da MIB privada inteira pode devolver centenas de linhas
e levar alguns minutos. Use --apenas-numericos para filtrar só o que
pode ser contador.
"""

import argparse
import asyncio
import logging

from collector import oids
from collector.snmp_client import walk_diagnostico


def _eh_numerico(valor: str) -> bool:
    try:
        int(valor.strip())
        return True
    except (AttributeError, ValueError):
        return False


async def _main(ip: str, base_oid: str, apenas_numericos: bool) -> None:
    print(f"\nContador universal (Printer-MIB) em {ip}:")
    padrao = await walk_diagnostico(ip, oids.OID_MARKER_LIFE_COUNT)
    if padrao:
        for sufixo, valor in sorted(padrao.items()):
            print(f"  {oids.OID_MARKER_LIFE_COUNT}.{sufixo} = {valor}")
    else:
        print("  (não respondeu - a impressora não expõe prtMarkerLifeCount)")

    print(f"\nVarrendo {base_oid} ...")
    valores = await walk_diagnostico(ip, base_oid)

    if not valores:
        print("  (nada retornado - OID não implementado ou SNMP sem acesso a este ramo)")
        return

    exibidos = 0
    for sufixo, valor in sorted(valores.items()):
        if apenas_numericos and not _eh_numerico(valor):
            continue
        print(f"  {base_oid}.{sufixo} = {valor}")
        exibidos += 1

    print(f"\n{exibidos} valor(es) exibido(s) de {len(valores)} encontrado(s).")
    print(
        "Compare os valores com os contadores do painel da impressora.\n"
        "O OID cujo valor bater com o contador de CÓPIAS deve ser gravado "
        "no .env do servidor:\n\n"
        "    SNMP_OID_CONTADOR_COPIAS=<oid completo, com o índice>\n\n"
        "A partir daí a coleta passa a preencher leitura_toner.paginas_copias."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Descobre os OIDs de contador de páginas de uma impressora Canon."
    )
    parser.add_argument("--ip", required=True, help="IP da impressora a inspecionar.")
    parser.add_argument(
        "--oid",
        default=oids.BASE_PRIVADA_CANON,
        help="Ramo a varrer (padrão: MIB privada Canon inteira).",
    )
    parser.add_argument(
        "--apenas-numericos",
        action="store_true",
        help="Mostra só valores inteiros - filtra o que pode ser contador.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    asyncio.run(_main(args.ip, args.oid, args.apenas_numericos))
