# Padrões do projeto

Este documento descreve os padrões **já adotados** no código existente.
Ele não propõe um estilo novo: foi escrito lendo o que já estava lá e
transformando em regra explícita. Toda criação ou alteração de arquivo
deve seguir o que está aqui.

---

## 1. Arquitetura: um pacote por domínio, três camadas

Cada domínio é um pacote com sempre os mesmos quatro arquivos:

```
<dominio>/
    __init__.py     docstring do domínio + reexporta as entidades
    entities.py     dataclasses e enums - o formato do dado
    repository.py   SQL puro - nada além de acesso a dado
    service.py      regra de negócio - nada de SQL
```

Domínios atuais: `printers`, `readings`, `collection_runs`, `toner_changes`.

`collector` é a exceção e é deliberada: não tem tabela própria, é o
processo que alimenta os outros. Seus arquivos são `oids.py`,
`snmp_client.py` e `service.py`.

### A regra de dependência

```
collector/service  ──> services dos domínios  ──> repositories  ──> database
```

- **Domínios não se conhecem entre si.** `readings` não importa `printers`.
  Quem amarra domínios é `collector/service.py`, e é só ele.
- **Service nunca escreve SQL.** Se você está escrevendo `SELECT` num
  service, o lugar certo é o repository.
- **Repository nunca decide regra.** Se está escrevendo um `if` que
  significa uma decisão de negócio, o lugar certo é o service.
- **Nada acima chama `database` diretamente**, exceto repositories.

Exemplo do limite: quem decide se uma coleta é `COMPLETED`, `PARTIAL` ou
`FAILED` é [collection_runs/service.py](../backend/collection_runs/service.py);
o repository só executa o `UPDATE` com o status que recebeu.

---

## 2. Entidades

- `@dataclass` simples, um campo por coluna, na ordem da tabela.
- Um `from_row(cls, row: dict)` que constrói a entidade a partir do
  `DictCursor` do aiomysql. É o único lugar que conhece nomes de coluna
  fora do repository.
- Colunas `ENUM` do banco viram `Enum` do Python, herdando de `str` (ou
  `int`), com docstring dizendo qual ENUM elas espelham. Isso faz o
  editor acusar erro de digitação em vez de o sistema falhar em produção.
- Conversões defensivas ficam no `from_row` (ex: `bool(row["active"])`,
  porque o driver pode devolver TINYINT como `0/1`).

---

## 3. Repositories

- Uma constante `_COLUNAS` no topo com a lista de colunas do `SELECT`,
  reaproveitada por todas as consultas do arquivo. Evita que uma coluna
  nova seja adicionada em três queries e esquecida na quarta.
- Sempre `async with get_connection() as conn:` — o commit/rollback é
  automático, não repita `try/except/commit` no repository.
- `aiomysql.DictCursor` quando o resultado vira entidade; cursor padrão
  quando é um escalar (`COUNT(*)`, `lastrowid`).
- **Valores sempre por placeholder `%s`**, nunca interpolados na string.
  A única interpolação permitida é a de nomes de coluna vindos de
  constantes do próprio código (`f"SELECT {_COLUNAS}"`).
- Funções nomeadas pelo que fazem no banco: `listar_`, `buscar_`,
  `inserir_`, `atualizar_`, `contar_`.
- Devolve entidades ou tipos primitivos. Nunca devolve cursor nem tupla crua.

---

## 4. Services

- Nomes voltados ao negócio, não ao banco: `listar_impressoras_ativas`,
  `registrar_se_houve_troca`, `finalizar_coleta`.
- Funções auxiliares privadas com `_` (`_decidir_status`, `_rendimento`).
- Funções puras (sem I/O) ficam separadas das `async` que tocam o banco.
  São elas que carregam a regra e são as mais fáceis de testar.
- Um service fino que só delega ao repository é aceitável e esperado —
  ele existe para ter onde crescer quando a regra aparecer.

---

## 5. Configuração

- **Nenhum `os.getenv` fora de [config.py](../backend/config.py).** Todo
  módulo faz `from config import settings`.
- Cada grupo de configuração é uma `@dataclass(frozen=True)`.
- Toda variável nova precisa de: campo na dataclass, leitura em
  `load_settings()`, validação em `_validar()` se houver faixa válida, e
  entrada no `.env.example` com comentário explicando.
- Erro de configuração falha na importação, com mensagem dizendo qual
  variável e o que se esperava dela.

---

## 6. Idioma e nomenclatura

- **Código e documentação em português.** Funções, variáveis, docstrings
  e comentários.
- **Exceções que ficam em inglês:** nomes de pacote e de arquivo
  (`printers`, `readings`, `repository.py`), termos consagrados de
  arquitetura (`repository`, `service`, `entities`) e nomes de coluna
  que já existem no banco.
- Nomes de tabela e coluna em português, como já está no schema
  (`impressora`, `leitura_toner`, `nivel_percentual`).
- `snake_case` para funções e variáveis, `PascalCase` para classes,
  `MAIÚSCULA_COM_UNDERSCORE` para constantes de módulo.

---

## 7. Docstrings: explique o PORQUÊ

Este é o padrão mais marcante do projeto e o mais importante de manter.
As docstrings existentes não descrevem o óbvio — elas registram a decisão
e o motivo dela.

Não escreva:
```python
def calcular_percentual(nivel, capacidade):
    """Calcula o percentual dividindo nível por capacidade."""
```

Escreva:
```python
def calcular_percentual(nivel, capacidade):
    """
    Retorna None (não é erro) quando o valor não é medível segundo a
    RFC 3805: nível negativo ou capacidade <= 0. Isso é comum em
    suprimentos como caixa de resíduo, que alguns modelos não
    reportam em percentual.
    """
```

Registre principalmente: por que uma alternativa óbvia **não** foi usada,
qual comportamento de hardware motivou o código, e o que quebraria se
alguém "simplificasse".

---

## 8. Assincronismo

- Todo I/O é `async`: banco e SNMP.
- Paralelismo com `asyncio.gather`, limitado por `asyncio.Semaphore` quando
  o alvo é a rede (`COLLECTOR_MAX_CONCURRENT_REQUESTS`).
- Uma impressora com problema nunca pode derrubar a coleta das outras:
  `_coletar_impressora` captura exceção e devolve erro em vez de propagar.

---

## 9. Tipagem

- Type hints em todas as assinaturas, incluindo retorno.
- Sintaxe moderna: `str | None`, `list[Impressora]`, `dict[str, str]`
  (requer Python 3.10+; o projeto assume 3.12+).
- Se uma função pode devolver `None`, o tipo precisa dizer isso.

---

## 10. Scripts e pontos de entrada

- Scripts executáveis ficam na raiz de `backend/`: `cli.py`,
  `teste_snmp.py`, `descobrir_oids_canon.py`.
- Todos usam `argparse` e um bloco `if __name__ == "__main__":` no fim.
- Módulos de biblioteca podem ter um smoke test manual no
  `if __name__ == "__main__":` — [config.py](../backend/config.py) e
  [database/connection.py](../backend/database/connection.py) fazem isso.
- Scripts SQL ficam em `backend/database/` com nome `scriptXxxYyy.sql`.

---

## 11. Imports

- Absolutos a partir da raiz de `backend/`: `from readings import service`.
  Isso significa que **os comandos são executados com `backend/` como
  diretório atual**.
- Quando um módulo importa dois services, use alias para não colidir:
  `from readings import service as readings_service`.
- Ordem: biblioteca padrão, terceiros, projeto — separados por linha em branco.

---

## 12. Testes

- Ficam em `backend/tests/`, um arquivo por módulo testado
  (`test_readings_service.py`, `test_collector_service.py`, ...).
- Rodam com `pytest`, a partir de `backend/`. A configuração está em
  `pytest.ini`.
- Agrupados em classes `TestAlgumaCoisa`, para que a saída do pytest já
  diga qual comportamento quebrou.
- **Nenhum teste depende do parque de impressoras nem do banco.** A
  consulta SNMP e os repositories são substituídos por dublês. Um teste
  que precisa da rede do hospital é um teste que ninguém roda.
- Dublês com `unittest.mock` (biblioteca padrão), não com o fixture
  `monkeypatch` do pytest — assim os testes continuam executáveis fora
  do pytest quando for preciso.
- O nome do teste descreve o comportamento em português, não a função:
  `test_caixa_de_residuo_nao_gera_troca`, não `test_detectar_trocas_2`.
- Quando o teste existe para impedir a volta de um bug específico, o
  comentário diz qual era o bug. Exemplo, em
  `test_nenhum_suprimento_some_quando_falta_par`: "com zip(), o
  suprimento sem par desaparecia silenciosamente".
- Testes que exigem hardware real são uma categoria à parte e ficam fora
  da suíte padrão — ver Fase 5B em [CRONOGRAMA.md](CRONOGRAMA.md).

---

## 13. Segurança

- `.env` nunca vai para o Git (já coberto pelo `.gitignore`);
  `.env.example` vai, sempre com valores de exemplo.
- Nenhum valor de usuário entra em SQL por concatenação.
- Logs não imprimem senha (ver o smoke test de `config.py`, que mostra
  usuário e host mas nunca a senha).
