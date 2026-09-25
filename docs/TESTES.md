# Testes automatizados

72 testes, em 5 arquivos, rodando em menos de um segundo. Nenhum deles
precisa do parque de impressoras, de rede ou de banco de dados.

Este documento explica o que está coberto, por que a suíte foi montada
assim, e o que deliberadamente ficou de fora.

---

## Como rodar

A partir de `backend/`:

```bash
pip install -r requirements.txt   # de preferência num venv
cp .env.example .env              # não precisa editar
pytest
```

Saída esperada:

```
tests\test_collection_runs_service.py .....                     [  6%]
tests\test_collector_service.py ..............                  [ 26%]
tests\test_readings_service.py ..........................       [ 62%]
tests\test_snmp_client.py ................                      [ 84%]
tests\test_toner_changes_service.py ...........                 [100%]

============================= 72 passed in 0.29s ==============================
```

### Duas coisas que confundem na primeira vez

**As dependências precisam estar instaladas, mesmo sem banco nem rede.**
Os testes importam os módulos do projeto, e esses importam `aiomysql` e
`pysnmp` no topo do arquivo. Nenhuma conexão chega a ser aberta — mas sem
os pacotes, o import falha antes de qualquer teste rodar.

**O `.env` precisa existir.** `config.py` valida a configuração no momento
da importação, então sem ele a suíte nem chega a coletar os testes: dá 5
erros que, à primeira vista, não parecem ter relação com o `.env`. Os
valores do `.env.example` servem como estão; não precisam ser reais nem
apontar para um banco que exista.

### Comandos úteis

```bash
pytest -v                                  # nome de cada teste
pytest tests/test_readings_service.py      # um arquivo só
pytest -k paralelo                         # só os testes de toner paralelo
pytest --collect-only -q                   # lista sem executar
pytest -x                                  # para no primeiro erro
```

---

## A decisão: por que nada depende do parque

Um teste que consulta as 66 impressoras de verdade tem três problemas, e
nenhum deles é sobre qualidade de código:

1. **Só roda dentro da rede do hospital.** A máquina de desenvolvimento
   não alcança as impressoras, então a suíte seria inútil justamente onde
   o código é escrito.
2. **Quebra por motivos que não são defeito.** Alguém desliga uma
   impressora no fim do expediente e o teste falha. Depois de algumas
   vezes, ninguém mais olha o resultado.
3. **Não consegue produzir os cenários que importam.** Não dá para pedir a
   uma impressora que simule uma troca de cartucho, que fique sem
   responder, ou que devolva uma MIB inconsistente — e são exatamente
   esses os caminhos com mais risco de bug.

Por isso a consulta SNMP e os repositories são substituídos por dublês, e
os cenários difíceis viram testes determinísticos: impressora que não
responde, cartucho paralelo, troca de toner, caixa de resíduo esvaziando,
coleta sobreposta, MIB com linha faltando.

Isso **não substitui** a validação em hardware, que continua necessária e
está registrada nas fases 4B e 5B do [CRONOGRAMA.md](CRONOGRAMA.md).
A suíte responde "a regra está correta?"; o hardware responde "a
impressora se comporta como a regra supõe?". São perguntas diferentes.

---

## Como os dublês funcionam

Os dublês usam `unittest.mock`, da biblioteca padrão, e **não** o fixture
`monkeypatch` do pytest. O motivo é prático: sem depender de fixture, os
testes continuam executáveis fora do pytest quando for preciso.

Para as funções puras não há dublê nenhum — recebem strings, devolvem
objetos, e são testadas diretamente.

Para o coletor, que amarra quatro domínios, existe a classe auxiliar
`ColetorDublado` em `test_collector_service.py`. Ela troca de uma vez os
oito pontos de contato do `collector/service.py` com o mundo externo:

```python
with ColetorDublado(impressoras, criar_resposta_snmp()) as dublê:
    await executar_coleta(TriggerType.MANUAL)

assert dublê.contagem_final["impressoras_sucesso"] == 2
```

O que ela substitui: `consultar_impressora`, a listagem de impressoras
ativas, o registro de marca/modelo, abertura e fechamento da coleta, a
gravação de leituras, a busca de leituras anteriores e o registro de
troca. A propriedade `contagem_final` expõe os argumentos com que
`finalizar_coleta` foi chamado — é assim que se verifica a contagem de
sucesso e falha sem banco.

Existe para evitar seis `with patch(...)` aninhados em cada teste.

---

## Mapa dos testes

### `test_readings_service.py` — 26 testes

O arquivo mais importante: cobre a interpretação dos valores crus do
SNMP, que é onde mora a maior parte da regra do projeto.

| Classe | Protege |
|---|---|
| `TestCalcularPercentual` | o cálculo e o arredondamento para uma casa; nível negativo e capacidade zero devolvem `None`, não erro |
| `TestIndiceDoSufixo` | o índice gravado vem do `supplyIndex` do OID, não da posição na resposta — é o que mantém a continuidade do histórico se uma linha deixar de ser reportada |
| `TestTonerParalelo` | as três formas de "não reportou nível" viram `nao_reportado` |
| `TestParqueObservado` | os valores reais colhidos na Fase 4B.2: T06 original (80/100) sai `ok`, T06 paralelo (-2/100) sai `nao_reportado` |
| `TestInterpretarLeitura` | leitura normal, valor corrompido (`erro`) e índice sem capacidade correspondente |
| `TestMontarLeiturasSnmp` | o casamento das tabelas da `prtMarkerSupplies` pela chave da linha |
| `TestSemResposta` | a leitura registrada quando a impressora inteira não responde |

### `test_toner_changes_service.py` — 11 testes

| Classe | Protege |
|---|---|
| `TestHouveTroca` | só salto de nível acima do limiar é troca; queda, oscilação pequena e primeira leitura não são |
| `TestRendimento` | o número que vai para a caixa do toner: diferença entre contadores, com `None` na primeira troca e em contador que regrediu |

O que está em jogo aqui: um falso positivo inventa um cartucho que
ninguém trocou; um falso negativo some com o rendimento de um cartucho
real.

### `test_collector_service.py` — 14 testes

Integração da orquestração, com rede e banco dublados.

| Classe | Protege |
|---|---|
| `TestContagemDeSucesso` | o que conta como sucesso, o que conta como falha, que resposta parcial conta como sucesso mas aparece no `error_summary`, e que uma impressora com erro não derruba as outras |
| `TestFiltroDeIps` | `--ip` restringe a rodada de verdade |
| `TestDeteccaoDeTroca` | quando a detecção é acionada e, principalmente, quando **não** é |
| `TestIdentificacaoDaImpressora` | marca e modelo descobertos por SNMP são gravados |
| `TestColetaSobreposta` | `ColetaEmAndamento` é propagada, e nenhuma impressora é consultada quando a coleta é recusada |

### `test_collection_runs_service.py` — 5 testes

`TestDecidirStatus` cobre a regra que distingue `PARTIAL` (algumas
impressoras não responderam — o dia a dia normal) de `FAILED` (nenhuma
respondeu, o que sugere problema de rede e merece investigação).

### `test_snmp_client.py` — 16 testes

`TestDeduzirMarca` fixa o comportamento da heurística que extrai a marca
do `sysDescr`, que é texto livre e varia por fabricante: marca no início,
no meio, em maiúsculas, `Hewlett-Packard` normalizado para `HP`, e o
fallback para o primeiro token quando o fabricante não está mapeado.

`TestContadoresDePagina` fixa como cada contador de página é consultado,
com a rede dublada: OIDs do `.env` por get, `prtMarkerLifeCount` por walk
só quando `SNMP_OID_CONTADOR_TOTAL` está vazio, e sem recair nele quando
está configurado.

`TestRespostaParcial` cobre a impressora que responde a parte das
consultas: o que voltou em branco é repetido uma vez (e só isso);
consulta essencial em branco (nível, capacidade, contadores) derruba a
impressora para falha; não essencial vira `None` listado em
`consultas_em_branco`; OID não implementado (vazio sem erro) não conta
como falha.

---

## Testes que existem por causa de um bug específico

Estes merecem atenção redobrada em qualquer refatoração: cada um
documenta um defeito real que já aconteceu.

| Teste | O que impede de voltar |
|---|---|
| `test_nenhum_suprimento_some_quando_falta_par` | o `zip()` do MVP truncava silenciosamente — três níveis com duas capacidades faziam um suprimento desaparecer sem erro |
| `test_casa_pelo_indice_do_oid` e `test_ordem_de_chegada_nao_importa` | o pareamento por posição dividia o nível do toner pela capacidade da caixa de resíduo, produzindo um percentual plausível e errado |
| `test_codigo_especial_da_rfc_3805` e `test_capacidade_desconhecida` | os códigos `-1/-2/-3` eram classificados como `ok` sem percentual, deixando o cartucho paralelo indistinguível de um suprimento não medível |
| `test_leitura_sem_nivel_nao_gera_troca` | um paralelo que voltasse a reportar nível criava uma troca fantasma |
| `test_oids_configurados_sao_lidos_por_get` | os contadores do `.env` eram lidos por walk, que a partir de um OID completo nunca devolve o próprio valor — `paginas_copias` saía sempre `NULL` (Fase 4B.3) |
| `test_toner_cartridge_do_parque_dispara_a_deteccao` | só o tipo 3 contava como toner, mas o parque inteiro reporta 21 (tonerCartridge) — nenhuma troca seria detectada (Fase 4B.4) |
| `test_nivel_em_branco_derruba_para_falha` e `test_nao_essencial_em_branco_segue_e_fica_registrada` | na coleta #1, uma consulta em branco virou `NULL` gravado como sucesso, sem registro do motivo (Fase 4B.5) |
| `test_caixa_de_residuo_nao_gera_troca` | a caixa de resíduo enche em vez de esvaziar, então todo esvaziamento parecia troca de toner |

---

## O que NÃO está coberto

Lista honesta, para ninguém confiar mais do que deve:

- **O SQL dos repositories.** Nenhum teste abre conexão. As consultas mais
  complexas (`buscar_anteriores_por_suprimento`,
  `buscar_ultimas_por_impressora`) e o índice UNIQUE que impede coleta
  sobreposta foram validados manualmente contra o MariaDB, num schema
  descartável — mas isso não está automatizado e não roda de novo sozinho.
- **`config.py`.** Os parsers `_get_env_oid` e `_get_env_horarios` foram
  verificados à mão, mas não têm teste. São bons candidatos a serem os
  próximos: são funções puras e a validação deles é o que impede um OID
  ou horário digitado errado passar despercebido.
- **`scheduler.py`.** Nem o `montar_agendador` nem o tratamento de erro do
  job têm teste.
- **`cli.py`.** Incluindo os códigos de saída, que o Agendador de Tarefas
  do Windows usaria para sinalizar falha.
- **`database/connection.py`.** O pool e o commit/rollback automático.
- **O comportamento real das impressoras.** Fase 4B do cronograma.
- **A coleta ponta a ponta contra o parque.** Fase 5B do cronograma.

---

## Escrevendo um teste novo

Convenções, todas visíveis nos arquivos existentes:

- Um arquivo por módulo testado: `test_<modulo>.py`.
- Agrupar em classes `TestAlgumaCoisa`, para que a saída do pytest já diga
  qual comportamento quebrou.
- O nome descreve o **comportamento** em português, não a função:
  `test_caixa_de_residuo_nao_gera_troca`, e não `test_detectar_trocas_2`.
- Se o teste existe para impedir a volta de um bug, o comentário diz qual
  era o bug — senão alguém "simplifica" o teste junto com o código.
- Nada de rede, banco ou arquivo. Se um teste novo precisar disso, ele
  pertence à Fase 5B, não a esta suíte.
- Teste `async` não precisa de marcador: o `pytest.ini` usa
  `asyncio_mode = auto`.
