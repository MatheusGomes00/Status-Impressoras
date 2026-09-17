"""
MVP simplificado - coleta via SNMP apenas:
  1. Número de série da impressora
  2. Nível(is) do toner

Uso:
    python mvp_snmp_simples.py --ip 192.168.1.50
    python mvp_snmp_simples.py --ip 192.168.1.50 --community minha_community
"""

import argparse
import asyncio

from pysnmp.hlapi.v3arch.asyncio import (
    walk_cmd,
    SnmpEngine,
    CommunityData,
    UdpTransportTarget,
    ContextData,
    ObjectType,
    ObjectIdentity,
)

OID_SERIAL_NUMBER = "1.3.6.1.2.1.43.5.1.1.17"      # tabela (sem índice fixo)
OID_SUPPLIES_LEVEL = "1.3.6.1.2.1.43.11.1.1.9"      # tabela de níveis de suprimento
OID_SUPPLIES_MAX_CAPACITY = "1.3.6.1.2.1.43.11.1.1.8"  # tabela de capacidade máxima


async def _walk_oid(ip: str, community: str, base_oid: str, port: int = 161) -> list[str]:
    """Faz o walk de uma tabela e retorna a lista de valores encontrados (em ordem)."""
    engine = SnmpEngine()
    transport = await UdpTransportTarget.create((ip, port), timeout=3, retries=1)

    valores = []
    walker = walk_cmd(
        engine,
        CommunityData(community, mpModel=0),  # mpModel=0 -> SNMPv1
        transport,
        ContextData(),
        ObjectType(ObjectIdentity(base_oid)),
    )

    async for errorIndication, errorStatus, errorIndex, varBinds in walker:
        if errorIndication or errorStatus:
            break
        for oid_obj, value in varBinds:
            if not str(oid_obj).startswith(base_oid):
                continue
            valores.append(value.prettyPrint())

    engine.close_dispatcher()
    return valores


async def get_serial_number(ip: str, community: str, port: int = 161) -> str:
    # Usamos walk (não get fixo em .1) porque muitas Canon não respondem
    # no índice esperado pela MIB padrão.
    valores = await _walk_oid(ip, community, OID_SERIAL_NUMBER, port)
    if not valores:
        return "ERRO (tabela vazia - dispositivo não expõe serial via Printer-MIB padrão)"
    return valores[0]


async def get_toner_percent(ip: str, community: str, port: int = 161) -> list[str]:
    niveis, capacidades = await asyncio.gather(
        _walk_oid(ip, community, OID_SUPPLIES_LEVEL, port),
        _walk_oid(ip, community, OID_SUPPLIES_MAX_CAPACITY, port),
    )

    resultado = []
    for nivel_str, cap_str in zip(niveis, capacidades):
        try:
            nivel = int(nivel_str)
            cap = int(cap_str)
        except ValueError:
            resultado.append("N/D")
            continue
        if nivel < 0 or cap <= 0:
            # -2/-3 são códigos especiais da RFC 3805 (não medível)
            resultado.append("N/D")
        else:
            resultado.append(f"{round((nivel / cap) * 100, 1)}%")
    return resultado


async def main(ip: str, community: str, port: int) -> None:
    serie = await get_serial_number(ip, community, port)
    percentuais = await get_toner_percent(ip, community, port)
    nivel_str = ", ".join(percentuais) if percentuais else "N/D"

    print(f"Série: {serie}\nNível do toner: {nivel_str}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MVP simplificado - série + nível de toner")
    parser.add_argument("--ip", required=True, help="IP da impressora")
    parser.add_argument("--community", default="public", help="Community string SNMP (padrão: public)")
    parser.add_argument("--port", type=int, default=161, help="Porta SNMP (padrão: 161)")
    args = parser.parse_args()

    asyncio.run(main(args.ip, args.community, args.port))