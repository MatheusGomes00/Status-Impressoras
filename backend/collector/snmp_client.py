"""
Cliente SNMP assíncrono - a camada que fala com a impressora.

É a evolução direta do MVP (teste_snmp.py), que já foi validado contra
o parque real. A mecânica de consulta é a mesma: walk_cmd da pysnmp com
CommunityData(mpModel=0) para SNMPv1, sobre UdpTransportTarget.

Duas diferenças em relação ao MVP, ambas deliberadas:

1. `_walk_oid` devolve {sufixo_do_oid: valor} em vez de uma lista de
   valores. O MVP descartava o OID e depois pareava as tabelas por
   posição, o que embaralha suprimentos quando uma tabela tem uma linha
   que a outra não tem. Guardando o sufixo, o casamento passa a ser pela
   chave real da linha na MIB.

2. O SnmpEngine é fechado em `finally`. No MVP ele era fechado só no
   caminho feliz; numa coleta de 66 impressoras, cada timeout deixaria
   um engine pendurado.

Nenhuma regra de interpretação mora aqui: este módulo entrega os valores
crus e quem decide o que eles significam é readings/service.py.
"""

import asyncio
import logging
from dataclasses import dataclass, field

from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    walk_cmd,
)

from collector import oids
from config import settings

logger = logging.getLogger(__name__)

_MARCAS_CONHECIDAS = (
    "canon", "hp", "hewlett-packard", "epson", "brother", "lexmark",
    "samsung", "kyocera", "ricoh", "xerox", "sharp", "oki", "konica",
)


@dataclass
class ColetaSnmp:
    """
    Resposta crua de uma impressora, antes de qualquer interpretação.

    `respondeu=False` significa que nenhuma das consultas trouxe valor
    algum - a impressora está fora do ar, fora da VLAN ou com SNMP
    desabilitado. É o que vira status 'sem_resposta' na leitura.
    """
    respondeu: bool
    erro: str | None = None
    numero_serie: str | None = None
    marca: str | None = None
    modelo: str | None = None
    niveis: dict[str, str] = field(default_factory=dict)
    capacidades: dict[str, str] = field(default_factory=dict)
    descricoes: dict[str, str] = field(default_factory=dict)
    tipos: dict[str, str] = field(default_factory=dict)
    paginas_total: int | None = None
    paginas_copias: int | None = None


@dataclass
class _ResultadoWalk:
    valores: dict[str, str]
    erro: str | None = None


async def _walk_oid(
    ip: str,
    community: str,
    base_oid: str,
    port: int,
    timeout: int,
    retries: int,
) -> _ResultadoWalk:
    """
    Faz o walk de uma tabela e devolve {sufixo_do_oid: valor}.

    O sufixo é o que sobra do OID depois da base - para
    "1.3.6.1.2.1.43.11.1.1.9.1.2" com base "1.3.6.1.2.1.43.11.1.1.9",
    o sufixo é "1.2". É a chave da linha na MIB, e é o que permite
    casar as tabelas de suprimento entre si.
    """
    engine = SnmpEngine()
    valores: dict[str, str] = {}
    erro: str | None = None

    try:
        transport = await UdpTransportTarget.create(
            (ip, port), timeout=timeout, retries=retries
        )

        walker = walk_cmd(
            engine,
            CommunityData(community, mpModel=0),  # mpModel=0 -> SNMPv1
            transport,
            ContextData(),
            ObjectType(ObjectIdentity(base_oid)),
        )

        async for errorIndication, errorStatus, errorIndex, varBinds in walker:
            if errorIndication or errorStatus:
                erro = str(errorIndication or errorStatus)
                break
            for oid_obj, value in varBinds:
                oid_str = str(oid_obj)
                if not oid_str.startswith(base_oid):
                    continue
                sufixo = oid_str[len(base_oid):].lstrip(".")
                valores[sufixo] = value.prettyPrint()

    except Exception as excecao:
        erro = f"{type(excecao).__name__}: {excecao}"
    finally:
        engine.close_dispatcher()

    return _ResultadoWalk(valores=valores, erro=erro)


def _primeiro_valor(resultado: _ResultadoWalk) -> str | None:
    """Devolve o primeiro valor de um walk de tabela, na ordem do índice."""
    if not resultado.valores:
        return None
    return resultado.valores[sorted(resultado.valores)[0]]


def _primeiro_inteiro(resultado: _ResultadoWalk) -> int | None:
    """Igual a _primeiro_valor, mas já convertido para int quando possível."""
    bruto = _primeiro_valor(resultado)
    if bruto is None:
        return None
    try:
        return int(bruto.strip())
    except ValueError:
        return None


def deduzir_marca(sys_descr: str | None) -> str | None:
    """
    Extrai a marca do texto livre do sysDescr.

    Procura primeiro numa lista de fabricantes conhecidos, porque o
    sysDescr nem sempre começa pelo nome da marca. Sem acerto na lista,
    cai no primeiro token - o que mantém a função útil para um
    fabricante que ainda não esteja mapeado, em vez de devolver nada.
    """
    if not sys_descr:
        return None

    texto = sys_descr.strip()
    minusculo = texto.lower()

    for marca in _MARCAS_CONHECIDAS:
        if marca in minusculo:
            return "HP" if marca == "hewlett-packard" else marca.capitalize()

    primeiro_token = texto.split()[0] if texto.split() else None
    return primeiro_token[:50] if primeiro_token else None


async def walk_diagnostico(
    ip: str,
    base_oid: str,
    community: str | None = None,
    port: int | None = None,
) -> dict[str, str]:
    """
    Walk cru de qualquer OID, para scripts de descoberta e diagnóstico.

    Existe para que descobrir_oids_canon.py não precise usar a função
    privada `_walk_oid` nem reimplementar a mecânica de consulta.
    """
    resultado = await _walk_oid(
        ip,
        community or settings.snmp.default_community,
        base_oid,
        port or settings.snmp.port,
        settings.snmp.timeout_seconds,
        settings.snmp.retries,
    )
    return resultado.valores


async def consultar_impressora(
    ip: str,
    community: str | None = None,
    port: int | None = None,
) -> ColetaSnmp:
    """
    Consulta uma impressora e devolve todos os dados crus de uma vez.

    As consultas são disparadas em paralelo (mesma estratégia do MVP,
    que já usava asyncio.gather para nível e capacidade) porque são
    independentes entre si - o custo de uma impressora é o da consulta
    mais lenta, não a soma de todas.
    """
    community = community or settings.snmp.default_community
    port = port or settings.snmp.port
    timeout = settings.snmp.timeout_seconds
    retries = settings.snmp.retries

    async def walk(base_oid: str) -> _ResultadoWalk:
        return await _walk_oid(ip, community, base_oid, port, timeout, retries)

    (
        serial,
        sys_descr,
        nome,
        niveis,
        capacidades,
        descricoes,
        tipos,
        paginas,
    ) = await asyncio.gather(
        walk(oids.OID_SERIAL_NUMBER),
        walk(oids.OID_SYS_DESCR),
        walk(oids.OID_PRINTER_NAME),
        walk(oids.OID_SUPPLIES_LEVEL),
        walk(oids.OID_SUPPLIES_MAX_CAPACITY),
        walk(oids.OID_SUPPLIES_DESCRIPTION),
        walk(oids.OID_SUPPLIES_TYPE),
        walk(oids.OID_MARKER_LIFE_COUNT),
    )

    resultados = (serial, sys_descr, nome, niveis, capacidades, descricoes, tipos, paginas)
    respondeu = any(resultado.valores for resultado in resultados)

    if not respondeu:
        erro = next((r.erro for r in resultados if r.erro), "sem resposta SNMP")
        logger.warning("Impressora %s não respondeu: %s", ip, erro)
        return ColetaSnmp(respondeu=False, erro=erro)

    texto_sys_descr = _primeiro_valor(sys_descr)
    # sysDescr é texto livre e pode vir com várias linhas de descrição;
    # a coluna `modelo` é VARCHAR(100), então corta aqui em vez de
    # deixar o INSERT falhar no meio de uma coleta.
    modelo = (_primeiro_valor(nome) or texto_sys_descr or "").strip()[:100] or None

    paginas_copias = None
    if oids.OID_CANON_CONTADOR_COPIAS:
        resultado_copias = await walk(oids.OID_CANON_CONTADOR_COPIAS)
        paginas_copias = _primeiro_inteiro(resultado_copias)

    return ColetaSnmp(
        respondeu=True,
        numero_serie=_primeiro_valor(serial),
        marca=deduzir_marca(texto_sys_descr),
        modelo=modelo,
        niveis=niveis.valores,
        capacidades=capacidades.valores,
        descricoes=descricoes.valores,
        tipos=tipos.valores,
        paginas_total=_primeiro_inteiro(paginas),
        paginas_copias=paginas_copias,
    )
