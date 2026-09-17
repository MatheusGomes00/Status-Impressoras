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


@dataclass(frozen=True)
class CollectorConfig:
    max_concurrent_requests: int
    # Quantos pontos percentuais o nível precisa SUBIR entre duas leituras
    # para ser considerado troca de cartucho. Existe porque o nível
    # reportado pela impressora oscila alguns pontos para cima sem que
    # nada tenha sido trocado (medição por estimativa, agitação do toner).
    limiar_troca_toner_pp: int


@dataclass(frozen=True)
class Settings:
    database: DatabaseConfig
    snmp: SnmpConfig
    collector: CollectorConfig


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
    )

    collector = CollectorConfig(
        max_concurrent_requests=_get_env_int(
            "COLLECTOR_MAX_CONCURRENT_REQUESTS", default=15
        ),
        limiar_troca_toner_pp=_get_env_int(
            "COLLECTOR_LIMIAR_TROCA_TONER_PP", default=20
        ),
    )

    settings = Settings(database=database, snmp=snmp, collector=collector)
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
    print(f"  Coletor: max_concurrent_requests="
          f"{settings.collector.max_concurrent_requests}")
