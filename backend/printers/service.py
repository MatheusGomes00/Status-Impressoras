"""
Service do domínio Impressoras.

Hoje esta camada é fina - a maioria das funções apenas delega para o
repository, sem regra de negócio adicional. Ela existe desde já (em
vez de o coletor/API chamarem o repository diretamente) porque é o
lugar certo para crescer quando surgir lógica que não seja "só SQL",
por exemplo:
  - validar formato de IP antes de cadastrar uma impressora nova
  - decidir se uma impressora deve ser considerada "sem monitoramento"
    depois de N tentativas de coleta falhas seguidas
  - regras de autorização (ex: só usuários de um certo perfil podem
    desativar uma impressora)

Nenhuma dessas regras existe ainda - são só exemplos do tipo de coisa
que pertenceria aqui, e não no repository (que deve continuar sendo
só acesso a dado) nem no controller da API (que deve continuar sendo
só tradução de HTTP).
"""

from printers import repository
from printers.entities import Impressora


async def listar_impressoras_ativas() -> list[Impressora]:
    """Retorna as impressoras ativas, na ordem usada pelo coletor e pela futura listagem da API."""
    return await repository.listar_ativas()


async def obter_impressora(id_impressora: int) -> Impressora | None:
    """Busca uma impressora pelo id."""
    return await repository.buscar_por_id(id_impressora)


async def obter_impressora_por_ip(ip_address: str) -> Impressora | None:
    """Busca uma impressora pelo IP."""
    return await repository.buscar_por_ip(ip_address)


async def contar_impressoras_ativas() -> int:
    """Retorna a quantidade de impressoras ativas."""
    return await repository.contar_ativas()


async def registrar_identificacao(
    id_impressora: int,
    marca: str | None,
    modelo: str | None,
) -> None:
    """Grava marca/modelo descobertos pelo coletor via SNMP."""
    await repository.atualizar_identificacao(id_impressora, marca, modelo)
