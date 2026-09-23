"""
Carrega e centraliza as configurações do backend a partir do arquivo .env.

Qualquer outro módulo do projeto que precisar de alguma configuração
(banco, SNMP, coletor) deve importar a instância `settings` deste
arquivo - nunca ler os.environ diretamente em outro lugar do código.
Isso evita espalhar `os.getenv(...)` pelo projeto inteiro e garante
um único ponto de validação, com erro claro se algo obrigatório faltar.

Uso em outros módulos:
    from config import settings

    print(settings.database.host)
    print(settings.snmp.default_community)
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Carrega o .env que estiver na raiz do projeto (mesmo diretório deste arquivo)
_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)


def _get_env(key: str, default: str | None = None, required: bool = False) -> str:
    valor = os.getenv(key, default)
    if required and not valor:
        raise RuntimeError(
            f"Variável de ambiente obrigatória '{key}' não foi definida. "
            f"Verifique o arquivo .env na raiz do projeto "
            f"(use .env.example como referência)."
        )
    return valor


def _get_env_int(key: str, default: int) -> int:
    valor = os.getenv(key)
    if valor is None or valor == "":
        return default
    try:
        return int(valor)
    except ValueError:
        raise RuntimeError(
            f"Variável de ambiente '{key}' deveria ser um número inteiro, "
            f"mas veio '{valor}'."
        )


def _get_env_oid(key: str) -> str | None:
    """
    Lê um OID opcional do ambiente, validando o formato.

    Valida na carga (e não no meio de uma coleta) porque um OID digitado
    errado só se manifestaria como "a impressora não respondeu esse
    contador", que é exatamente o sintoma de um OID inexistente - e aí
    ninguém descobriria que a causa foi um ponto a mais no .env.
    """
    valor = os.getenv(key, "").strip()
    if not valor:
        return None

    if not all(parte.isdigit() for parte in valor.split(".")):
        raise RuntimeError(
            f"Variável de ambiente '{key}' deveria ser um OID numérico "
            f"(ex: 1.3.6.1.4.1.1602.1.11.1.3.1.4.1), mas veio '{valor}'."
        )
    return valor


def _get_env_horarios(key: str, default: str) -> tuple[tuple[int, int], ...]:
    """
    Lê uma lista de horários "HH:MM,HH:MM" e devolve pares (hora, minuto).

    Valida na carga para que um horário digitado errado derrube o
    agendador na partida, com mensagem clara, em vez de silenciosamente
    nunca disparar a coleta daquele horário.
    """
    bruto = os.getenv(key, "").strip() or default

    horarios: list[tuple[int, int]] = []
    for pedaco in bruto.split(","):
        pedaco = pedaco.strip()
        if not pedaco:
            continue
        try:
            hora_str, minuto_str = pedaco.split(":")
            hora, minuto = int(hora_str), int(minuto_str)
        except ValueError:
            raise RuntimeError(
                f"Variável de ambiente '{key}' tem o horário '{pedaco}' em "
                f"formato inválido. Use HH:MM separados por vírgula "
                f"(ex: 09:00,13:00,17:00)."
            )
        if not (0 <= hora <= 23 and 0 <= minuto <= 59):
            raise RuntimeError(
                f"Variável de ambiente '{key}' tem o horário '{pedaco}' fora "
                f"da faixa válida (00:00 a 23:59)."
            )
        horarios.append((hora, minuto))

    return tuple(horarios)


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    pool_min_size: int
    pool_max_size: int


@dataclass(frozen=True)
class SnmpConfig:
    default_community: str
    timeout_seconds: int
    retries: int
    port: int
    # OID do contador de CÓPIAS. Fica em configuração, e não em
    # collector/oids.py, porque é o único OID da coleta que não é padrão:
    # a Printer-MIB não separa cópia de impressão, esse contador só existe
    # na MIB privada do fabricante e varia por série do equipamento.
    # Deixar aqui permite preencher no servidor, depois de descobrir o
    # valor com descobrir_oids_canon.py, sem alterar código.
    # Vazio = não coletar cópias (paginas_copias fica NULL).
    oid_contador_copias: str | None


@dataclass(frozen=True)
class CollectorConfig:
    max_concurrent_requests: int
    # Quantos pontos percentuais o nível precisa SUBIR entre duas leituras
    # para ser considerado troca de cartucho. Existe porque o nível
    # reportado pela impressora oscila alguns pontos para cima sem que
    # nada tenha sido trocado (medição por estimativa, agitação do toner).
    limiar_troca_toner_pp: int
    # Após quantos minutos uma coleta presa em RUNNING é considerada
    # processo morto e encerrada como FAILED, liberando as próximas.
    timeout_coleta_minutos: int


@dataclass(frozen=True)
class SchedulerConfig:
    # Horários das coletas diárias, como pares (hora, minuto).
    horarios: tuple[tuple[int, int], ...]
    timezone: str


@dataclass(frozen=True)
class Settings:
    database: DatabaseConfig
    snmp: SnmpConfig
    collector: CollectorConfig
    scheduler: SchedulerConfig


def _validar(settings: "Settings") -> None:
    """Validações de sanidade além do tipo (já garantido por _get_env_int)."""
    db = settings.database
    if db.pool_min_size > db.pool_max_size:
        raise RuntimeError(
            f"DB_POOL_MIN_SIZE ({db.pool_min_size}) não pode ser maior que "
            f"DB_POOL_MAX_SIZE ({db.pool_max_size})."
        )
    if db.port <= 0:
        raise RuntimeError(f"DB_PORT inválido: {db.port}")

    snmp = settings.snmp
    if snmp.timeout_seconds <= 0:
        raise RuntimeError(f"SNMP_TIMEOUT_SECONDS inválido: {snmp.timeout_seconds}")
    if snmp.retries < 0:
        raise RuntimeError(f"SNMP_RETRIES inválido: {snmp.retries}")

    if settings.collector.max_concurrent_requests <= 0:
        raise RuntimeError(
            f"COLLECTOR_MAX_CONCURRENT_REQUESTS inválido: "
            f"{settings.collector.max_concurrent_requests}"
        )
    if not 1 <= settings.collector.limiar_troca_toner_pp <= 100:
        raise RuntimeError(
            f"COLLECTOR_LIMIAR_TROCA_TONER_PP deve estar entre 1 e 100, "
            f"mas veio {settings.collector.limiar_troca_toner_pp}."
        )
    if settings.collector.timeout_coleta_minutos <= 0:
        raise RuntimeError(
            f"COLLECTOR_TIMEOUT_COLETA_MINUTOS inválido: "
            f"{settings.collector.timeout_coleta_minutos}"
        )

    if not settings.scheduler.horarios:
        raise RuntimeError(
            "SCHEDULER_HORARIOS não pode ficar vazio: informe ao menos um "
            "horário no formato HH:MM (ex: 09:00,13:00,17:00)."
        )


def load_settings() -> Settings:
    """Monta o objeto de configuração completo, lendo e validando o .env."""
    database = DatabaseConfig(
        host=_get_env("DB_HOST", required=True),
        port=_get_env_int("DB_PORT", default=3306),
        user=_get_env("DB_USER", required=True),
        password=_get_env("DB_PASSWORD", default=""),
        database=_get_env("DB_NAME", required=True),
        pool_min_size=_get_env_int("DB_POOL_MIN_SIZE", default=2),
        pool_max_size=_get_env_int("DB_POOL_MAX_SIZE", default=10),
    )

    snmp = SnmpConfig(
        default_community=_get_env("SNMP_DEFAULT_COMMUNITY", default="public"),
        timeout_seconds=_get_env_int("SNMP_TIMEOUT_SECONDS", default=3),
        retries=_get_env_int("SNMP_RETRIES", default=1),
        port=_get_env_int("SNMP_PORT", default=161),
        oid_contador_copias=_get_env_oid("SNMP_OID_CONTADOR_COPIAS"),
    )

    collector = CollectorConfig(
        max_concurrent_requests=_get_env_int(
            "COLLECTOR_MAX_CONCURRENT_REQUESTS", default=15
        ),
        limiar_troca_toner_pp=_get_env_int(
            "COLLECTOR_LIMIAR_TROCA_TONER_PP", default=20
        ),
        timeout_coleta_minutos=_get_env_int(
            "COLLECTOR_TIMEOUT_COLETA_MINUTOS", default=30
        ),
    )

    scheduler = SchedulerConfig(
        horarios=_get_env_horarios("SCHEDULER_HORARIOS", default="09:00,13:00,17:00"),
        timezone=_get_env("SCHEDULER_TIMEZONE", default="America/Sao_Paulo"),
    )

    settings = Settings(
        database=database,
        snmp=snmp,
        collector=collector,
        scheduler=scheduler,
    )
    _validar(settings)
    return settings


# Instância única, carregada uma vez no momento da importação do módulo.
# Outros módulos do projeto importam assim: from config import settings
settings = load_settings()


if __name__ == "__main__":
    # Smoke test manual: roda "python config.py" para conferir rapidamente
    # se o .env está sendo lido e validado corretamente, sem senha exposta.
    print("Configuração carregada com sucesso:\n")
    print(f"  DB: {settings.database.user}@{settings.database.host}:"
          f"{settings.database.port}/{settings.database.database}")
    print(f"  Pool: min={settings.database.pool_min_size} "
          f"max={settings.database.pool_max_size}")
    print(f"  SNMP: community='{settings.snmp.default_community}' "
          f"timeout={settings.snmp.timeout_seconds}s retries={settings.snmp.retries}")
    print(f"  Contador de cópias: "
          f"{settings.snmp.oid_contador_copias or 'não configurado (paginas_copias ficará NULL)'}")
    print(f"  Coletor: max_concurrent_requests="
          f"{settings.collector.max_concurrent_requests} "
          f"limiar_troca={settings.collector.limiar_troca_toner_pp}pp "
          f"timeout_coleta={settings.collector.timeout_coleta_minutos}min")
    horarios = ", ".join(
        f"{hora:02d}:{minuto:02d}" for hora, minuto in settings.scheduler.horarios
    )
    print(f"  Agendador: {horarios} ({settings.scheduler.timezone}), todos os dias")
