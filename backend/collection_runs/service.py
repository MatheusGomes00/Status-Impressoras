"""
Service do domínio Execuções de Coleta.

Aqui mora a regra de negócio que decide o `status` final de uma
execução de coleta - o repository só executa o UPDATE, quem decide
qual status vai nesse UPDATE é este arquivo.

Regra de decisão do status:
  - total_impressoras == 0            -> FAILED
    (nada foi tentado - provavelmente falhou antes de conseguir sequer
    buscar a lista de impressoras ativas no banco)
  - impressoras_sucesso == total      -> COMPLETED
    (todas responderam com sucesso)
  - impressoras_sucesso == 0          -> FAILED
    (nenhuma respondeu - sugere problema geral, não específico de uma
    impressora isolada: rede caiu, VLAN bloqueada, etc.)
  - qualquer outro caso               -> PARTIAL
    (algumas responderam, outras não - o cenário mais comum no dia a dia)
"""

from collection_runs import repository
from collection_runs.entities import ColetaExecutada, CollectionStatus, TriggerType


def _decidir_status(total: int, sucesso: int) -> CollectionStatus:
    if total == 0:
        return CollectionStatus.FAILED
    if sucesso == total:
        return CollectionStatus.COMPLETED
    if sucesso == 0:
        return CollectionStatus.FAILED
    return CollectionStatus.PARTIAL


async def iniciar_coleta(trigger_type: TriggerType) -> ColetaExecutada:
    """
    Registra o início de uma nova execução de coleta e retorna a
    entidade completa (já buscando de volta do banco, para pegar o
    `iniciado_em` gerado pelo NOW() do servidor - evita depender do
    relógio local da máquina que está rodando o coletor).
    """
    novo_id = await repository.criar_run(trigger_type)
    coleta = await repository.buscar_por_id(novo_id)

    if coleta is None:
        # Não deveria acontecer nunca (acabamos de inserir), mas se
        # acontecer é sinal de algo grave o suficiente para não seguir
        # a coleta sem saber o id da execução.
        raise RuntimeError(
            f"Falha ao recuperar a coleta recém-criada (id={novo_id})."
        )

    return coleta


async def finalizar_coleta(
    id_coleta: int,
    total_impressoras: int,
    impressoras_sucesso: int,
    impressora_falha: int,
    error_summary: str | None = None,
) -> ColetaExecutada:
    """
    Decide o status final com base nos contadores e persiste o
    resultado. Retorna a entidade já atualizada.
    """
    status = _decidir_status(total_impressoras, impressoras_sucesso)

    await repository.finalizar_run(
        id_coleta=id_coleta,
        status=status,
        total_impressoras=total_impressoras,
        impressoras_sucesso=impressoras_sucesso,
        impressora_falha=impressora_falha,
        error_summary=error_summary,
    )

    coleta = await repository.buscar_por_id(id_coleta)
    if coleta is None:
        raise RuntimeError(
            f"Falha ao recuperar a coleta finalizada (id={id_coleta})."
        )

    return coleta


async def obter_ultima_coleta() -> ColetaExecutada | None:
    """Retorna a execução de coleta mais recente, ou None se nunca rodou nenhuma."""
    return await repository.buscar_ultima()


async def listar_coletas_recentes(limite: int = 20) -> list[ColetaExecutada]:
    """Retorna as últimas execuções de coleta, mais recentes primeiro."""
    return await repository.listar_recentes(limite)
