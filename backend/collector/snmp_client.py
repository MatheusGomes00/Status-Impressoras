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
   um engine pendurado. E é um por impressora, não um por consulta:
   criá-lo é caro e síncrono (ver consultar_impressora).

Nenhuma regra de interpretação mora aqui: este módulo entrega os valores
crus e quem decide o que eles significam é readings/service.py.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    get_cmd,
    walk_cmd,
)
from pysnmp.proto.rfc1905 import EndOfMibView, NoSuchInstance, NoSuchObject

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

    `respondeu=False` significa que não há dado confiável para gravar:
    ou nenhuma consulta trouxe valor (impressora fora do ar, fora da
    VLAN ou com SNMP desabilitado), ou uma consulta ESSENCIAL voltou em
    branco mesmo após nova tentativa. É o que vira 'sem_resposta'.

    `consultas_em_branco` lista as consultas NÃO essenciais que falharam
    numa impressora que respondeu: o campo correspondente fica None e a
    coleta segue, mas a falha fica registrada no error_summary.
    """
    respondeu: bool
    erro: str | None = None
    consultas_em_branco: list[str] = field(default_factory=list)
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

    @property
    def em_branco(self) -> bool:
        """
        True quando a consulta falhou sem trazer nada.

        Vazio SEM erro é o agente dizendo que não implementa o OID - é
        ausência legítima. Vazio COM erro (timeout, em geral) é falha de
        transporte: o dado existe, só não chegou.
        """
        return not self.valores and self.erro is not None


async def _walk_oid(
    ip: str,
    community: str,
    base_oid: str,
    port: int,
    timeout: int,
    retries: int,
    engine: SnmpEngine | None = None,
) -> _ResultadoWalk:
    """
    Faz o walk de uma tabela e devolve {sufixo_do_oid: valor}.

    O sufixo é o que sobra do OID depois da base - para
    "1.3.6.1.2.1.43.11.1.1.9.1.2" com base "1.3.6.1.2.1.43.11.1.1.9",
    o sufixo é "1.2". É a chave da linha na MIB, e é o que permite
    casar as tabelas de suprimento entre si.
    """
    # Sem engine recebido, cria e fecha o seu (uso avulso, como no
    # walk_diagnostico); recebido, quem criou é quem fecha.
    proprio = engine is None
    engine = engine or SnmpEngine()
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
            # O padrão (True) segue o walk até o fim da MIB do agente, não
            # até o fim da tabela: na Canon eram ~1000 linhas por walk para
            # aproveitar uma, e a coleta das 66 levava quase 10 minutos.
            lexicographicMode=False,
        )

        async for errorIndication, errorStatus, errorIndex, varBinds in walker:
            if errorIndication or errorStatus:
                erro = str(errorIndication or errorStatus)
                break
            for oid_obj, value in varBinds:
                oid_str = str(oid_obj)
                # Com o ponto: sem ele, a base "...4.1" casaria "...4.101".
                if not oid_str.startswith(base_oid + "."):
                    continue
                sufixo = oid_str[len(base_oid):].lstrip(".")
                valores[sufixo] = value.prettyPrint()

    except Exception as excecao:
        erro = f"{type(excecao).__name__}: {excecao}"
    finally:
        if proprio:
            engine.close_dispatcher()

    return _ResultadoWalk(valores=valores, erro=erro)


async def _get_oid(
    ip: str,
    community: str,
    oid: str,
    port: int,
    timeout: int,
    retries: int,
    engine: SnmpEngine | None = None,
) -> _ResultadoWalk:
    """
    Lê um único OID completo (com o índice da linha) e devolve {"": valor}.

    Existe para os contadores do .env, que são configurados já com o
    índice: um walk a partir de um OID folha devolve o que vem DEPOIS
    dele, nunca ele mesmo, e o contador saía sempre None. O formato de
    retorno é o mesmo do walk para que _primeiro_inteiro sirva aos dois.
    """
    # Sem engine recebido, cria e fecha o seu (uso avulso, como no
    # walk_diagnostico); recebido, quem criou é quem fecha.
    proprio = engine is None
    engine = engine or SnmpEngine()
    valores: dict[str, str] = {}
    erro: str | None = None

    try:
        transport = await UdpTransportTarget.create(
            (ip, port), timeout=timeout, retries=retries
        )
        errorIndication, errorStatus, _, varBinds = await get_cmd(
            engine,
            CommunityData(community, mpModel=0),  # mpModel=0 -> SNMPv1
            transport,
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
        )
        # Em SNMPv1, OID inexistente chega como errorStatus noSuchName;
        # os marcadores NoSuch*/EndOfMibView cobrem agentes que respondem
        # no estilo v2c mesmo assim. Nos dois casos é ausência legítima
        # (valores vazios, sem erro), não falha: só timeout e afins
        # contam como consulta que voltou em branco.
        if errorIndication:
            erro = str(errorIndication)
        elif errorStatus:
            if errorStatus.prettyPrint() != "noSuchName":
                erro = errorStatus.prettyPrint()
        else:
            for _, value in varBinds:
                if not isinstance(value, (NoSuchObject, NoSuchInstance, EndOfMibView)):
                    valores[""] = value.prettyPrint()
    except Exception as excecao:
        erro = f"{type(excecao).__name__}: {excecao}"
    finally:
        if proprio:
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


# Consultas sem as quais a leitura gravada ficaria errada, e não apenas
# incompleta. Nível e capacidade formam o percentual: sem um deles, toda
# linha viraria status 'erro' ou sumiria. Os contadores são o que a troca
# de toner congela: uma troca detectada numa leitura sem contador perde
# o rendimento deste cartucho e do próximo - e não há como recuperar,
# porque a leitura seguinte já compara contra esta. Melhor registrar a
# impressora como falha e deixar a próxima coleta detectar a troca.
_CONSULTAS_ESSENCIAIS = frozenset(
    {"niveis", "capacidades", "paginas_total", "paginas_copias"}
)


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

    Resposta parcial: se a impressora respondeu a alguma consulta, as
    que voltaram em branco são repetidas uma vez - ela está no ar, então
    a falha foi de transporte. Persistindo, consulta essencial em branco
    derruba a impressora para falha; não essencial vira campo None,
    listado em `consultas_em_branco`. Não é o retry que a Fase 6 decidiu
    não fazer: aquele seria para impressora que não respondeu nada.
    """
    # Um SnmpEngine por impressora, compartilhado pelas consultas dela.
    # Criar um engine custa ~0,09 s de CPU síncrona, que trava o event
    # loop: com um por consulta eram ~600 na coleta, quase um minuto em
    # que nenhuma outra impressora avançava.
    engine = SnmpEngine()
    try:
        return await _consultar(
            ip,
            community or settings.snmp.default_community,
            port or settings.snmp.port,
            engine,
        )
    finally:
        engine.close_dispatcher()


async def _consultar(
    ip: str, community: str, port: int, engine: SnmpEngine
) -> ColetaSnmp:
    timeout = settings.snmp.timeout_seconds
    retries = settings.snmp.retries

    def walk(base_oid: str) -> Callable[[], Awaitable[_ResultadoWalk]]:
        return lambda: _walk_oid(
            ip, community, base_oid, port, timeout, retries, engine
        )

    def get(oid: str) -> Callable[[], Awaitable[_ResultadoWalk]]:
        return lambda: _get_oid(ip, community, oid, port, timeout, retries, engine)

    consultas = {
        "numero_serie": walk(oids.OID_SERIAL_NUMBER),
        "sys_descr": walk(oids.OID_SYS_DESCR),
        "nome": walk(oids.OID_PRINTER_NAME),
        "niveis": walk(oids.OID_SUPPLIES_LEVEL),
        "capacidades": walk(oids.OID_SUPPLIES_MAX_CAPACITY),
        "descricoes": walk(oids.OID_SUPPLIES_DESCRIPTION),
        "tipos": walk(oids.OID_SUPPLIES_TYPE),
        # Configurado no .env, o total vem por get do OID exato; senão,
        # walk da tabela prtMarkerLifeCount. Sem recair no padrão quando o
        # OID configurado não responde: as duas fontes divergem, e
        # misturá-las entre coletas faria paginas_rendidas somar ou perder
        # a diferença.
        "paginas_total": (
            get(settings.snmp.oid_contador_total)
            if settings.snmp.oid_contador_total
            else walk(oids.OID_MARKER_LIFE_COUNT)
        ),
    }
    # O contador de cópias só existe na MIB privada do fabricante e vem
    # do .env (SNMP_OID_CONTADOR_COPIAS). Sem ele configurado, não é
    # consultado e `paginas_copias` fica NULL.
    if settings.snmp.oid_contador_copias:
        consultas["paginas_copias"] = get(settings.snmp.oid_contador_copias)

    nomes = list(consultas)
    respostas = await asyncio.gather(*(consultas[nome]() for nome in nomes))
    resultados = dict(zip(nomes, respostas))

    if not any(resultado.valores for resultado in resultados.values()):
        erro = next(
            (r.erro for r in resultados.values() if r.erro), "sem resposta SNMP"
        )
        logger.warning("Impressora %s não respondeu: %s", ip, erro)
        return ColetaSnmp(respondeu=False, erro=erro)

    em_branco = [nome for nome in nomes if resultados[nome].em_branco]
    if em_branco:
        logger.info(
            "Impressora %s: repetindo consultas em branco: %s",
            ip, ", ".join(em_branco),
        )
        novas = await asyncio.gather(*(consultas[nome]() for nome in em_branco))
        resultados.update(zip(em_branco, novas))
        em_branco = [nome for nome in em_branco if resultados[nome].em_branco]

    essenciais = [nome for nome in em_branco if nome in _CONSULTAS_ESSENCIAIS]
    if essenciais:
        erro = (
            f"resposta parcial, sem {', '.join(essenciais)}: "
            f"{resultados[essenciais[0]].erro}"
        )
        logger.warning("Impressora %s: %s", ip, erro)
        return ColetaSnmp(respondeu=False, erro=erro)

    if em_branco:
        logger.warning(
            "Impressora %s: consultas em branco: %s", ip, ", ".join(em_branco)
        )

    texto_sys_descr = _primeiro_valor(resultados["sys_descr"])
    # sysDescr é texto livre e pode vir com várias linhas de descrição;
    # a coluna `modelo` é VARCHAR(100), então corta aqui em vez de
    # deixar o INSERT falhar no meio de uma coleta.
    modelo = (
        _primeiro_valor(resultados["nome"]) or texto_sys_descr or ""
    ).strip()[:100] or None
    copias = resultados.get("paginas_copias")

    return ColetaSnmp(
        respondeu=True,
        consultas_em_branco=em_branco,
        numero_serie=_primeiro_valor(resultados["numero_serie"]),
        marca=deduzir_marca(texto_sys_descr),
        modelo=modelo,
        niveis=resultados["niveis"].valores,
        capacidades=resultados["capacidades"].valores,
        descricoes=resultados["descricoes"].valores,
        tipos=resultados["tipos"].valores,
        paginas_total=_primeiro_inteiro(resultados["paginas_total"]),
        paginas_copias=_primeiro_inteiro(copias) if copias else None,
    )
