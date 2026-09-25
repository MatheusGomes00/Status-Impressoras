# Cronograma de implementação

Documento vivo. Atualizar o estado de cada fase conforme o trabalho avança.

**Última atualização:** 2026-09-22

| Fase | Entrega | Estado |
|------|---------|--------|
| 1 | Configuração e conexão com o banco | Concluída |
| 2 | Schema e domínios em camadas | Concluída |
| 3 | Interpretação das leituras SNMP | Concluída |
| 4 | Coletor SNMP + CLI | Concluída |
| **4B** | **Validação em hardware (inclui toner paralelo)** | **Bloqueada — precisa do parque** |
| 5 | Testes automatizados sem o parque | Concluída |
| **5B** | **Teste de integração com o parque** | **Bloqueada — precisa do parque** |
| 6 | Agendador | Código concluído — falta decidir o processo no SO |
| 7 | API HTTP | Não iniciada |
| 8 | Relatórios e apresentação | Não iniciada — stack indefinida |

As fases 4B e 5B são as únicas que dependem de acesso às impressoras.
Tudo o mais pode ser desenvolvido e testado na máquina de desenvolvimento.

---

## Fases concluídas

### Fase 1 — Configuração e conexão
`config.py` centraliza o `.env`; `database/connection.py` mantém o pool
aiomysql com commit/rollback automático.

### Fase 2 — Schema e domínios
`scriptCriarTabelas.sql` cria `impressora`, `coleta_executada`,
`leitura_toner` e `troca_toner`. Domínios `printers`, `readings`,
`collection_runs` e `toner_changes` em três camadas.

### Fase 3 — Interpretação das leituras
`readings/service.py` converte os valores crus do SNMP em leituras
persistíveis: percentual, códigos especiais da RFC 3805, identificação de
suprimento e classificação de cartucho paralelo.
Detalhado em [COLETA_SNMP.md](COLETA_SNMP.md).

### Fase 4 — Coletor SNMP
`collector/` (OIDs, cliente SNMP, orquestração) e `cli.py`. Detecção de
troca de cartucho integrada ao fluxo de coleta.

### Fase 5 — Testes automatizados sem o parque
72 testes em `backend/tests/`, cobrindo interpretação das leituras,
detecção de troca, decisão de status da coleta, dedução de marca e a
orquestração completa do coletor.

```bash
cd backend
pytest
```

Mapa completo do que cada teste protege, e a lista do que ficou **fora**
da cobertura, em [TESTES.md](TESTES.md).

**Decisão registrada: os testes não dependem do parque.**

Um teste que consulta as 66 impressoras de verdade só roda dentro da rede
do hospital, quebra quando alguém desliga uma máquina, e não consegue
reproduzir um cenário sob demanda — não dá para pedir a uma impressora
que simule uma troca de cartucho no meio do expediente. A consequência
prática seria uma suíte que ninguém roda, porque falha por motivos que
não são defeito do código.

Por isso a consulta SNMP e os repositories são substituídos por dublês
(`unittest.mock`), e os cenários difíceis viram testes determinísticos:
impressora que não responde, cartucho paralelo, troca de toner, caixa de
resíduo esvaziando, coleta sobreposta. Rodam em qualquer máquina,
inclusive nesta, que não alcança o parque.

Os dublês usam `unittest.mock` da biblioteca padrão, e não o fixture
`monkeypatch`, para que os testes continuem executáveis fora do pytest
quando necessário.

Isso **não substitui** a validação em hardware — ela é necessária e está
nas fases 4B e 5B abaixo.

### Fase 6 — Agendador
`scheduler.py` dispara a coleta às **09:00, 13:00 e 17:00, todos os dias
da semana** (configurável em `SCHEDULER_HORARIOS`). Chama a mesma função
que o `cli.py`, então coleta manual e agendada não podem divergir.

**Sem lógica de retry**, por decisão: primeiro medir a taxa de
`sem_resposta` em produção, depois decidir se a complexidade se paga. Uma
impressora desligada à noite vai falhar de qualquer jeito; retry só
resolveria falha transitória de rede.

Falta apenas decidir como o processo roda no servidor — ver
"Fase 6 — decisão pendente" mais abaixo.

---

## Coleta sobreposta: como foi resolvido

O problema: o agendador dispara às 09h e alguém roda `cli.py` na mão no
mesmo minuto. São **dois processos diferentes**, então qualquer controle
em memória (lock, flag, variável) não resolve. Duas coletas simultâneas
competiriam pela mesma rede e gravariam leituras concorrentes para os
mesmos suprimentos.

Também não bastava "consultar antes de inserir": entre a consulta e o
INSERT existe uma janela em que os dois processos passam pela checagem.

**A solução é um índice UNIQUE no banco.** A tabela `coleta_executada`
ganhou uma coluna gerada que vale `1` enquanto o status é `RUNNING` e
`NULL` em qualquer outro status:

```sql
execucao_exclusiva TINYINT UNSIGNED
    AS (IF(status = 'RUNNING', 1, NULL)) VIRTUAL,
CONSTRAINT uq_uma_coleta_em_execucao UNIQUE (execucao_exclusiva)
```

Como o MariaDB não considera `NULL`s duplicados num índice UNIQUE, só
pode existir **uma** linha `RUNNING` por vez; as finalizadas ficam
livres. A segunda tentativa falha no INSERT e **nenhum registro parcial
chega a ser criado** — era o requisito de "não afetar os registros".

O repository traduz o erro do driver na exceção de domínio
`ColetaEmAndamento`. O `cli.py` avisa o operador na tela; o agendador
registra que pulou a rodada e segue.

**Recuperação de coleta travada.** Se o processo morrer no meio (queda de
energia, reboot), a linha `RUNNING` órfã bloquearia todas as coletas
seguintes. Antes de desistir, o service verifica se a coleta presa passou
de `COLLECTOR_TIMEOUT_COLETA_MINUTOS` (padrão 30) e, nesse caso, fecha
como `FAILED` e tenta de novo. O limite é generoso de propósito: uma
coleta normal termina em menos de um minuto, então qualquer `RUNNING` com
dezenas de minutos é processo morto, não coleta lenta.

No `scheduler.py` há ainda duas camadas em memória, que cobrem cenários
que o banco não vê: `max_instances=1` (coleta que demora mais que o
intervalo entre horários) e `coalesce=True` (máquina que hiberna e
acumula disparos vencidos).

---

## Fase 4B — Validação em hardware

**Bloqueada até haver acesso ao parque.** Nada aqui pode ser antecipado
da máquina de desenvolvimento, que não alcança a rede das impressoras.

### 4B.1 — Conferir o nível contra o painel

```bash
python cli.py --ip <IP> --dry-run
```

Compare o percentual de cada suprimento com o painel da impressora. É a
validação de que o pareamento por índice de OID resolveu a divergência do
MVP.

Se ainda divergir, a hipótese seguinte é a Canon reportar nível em escala
grosseira — o SNMP estaria certo e o painel arredondado. Confirmar num
cartucho recém-trocado, que deveria dar perto de 100%.

**Adaptação possível:** se o desvio for sistemático, decidir qual fonte é
a verdade antes de a série histórica crescer.

### 4B.2 — Como o parque reage a um toner paralelo

Esta é a verificação mais importante da fase, e a que tem mais hipótese
não confirmada embutida.

O código classifica como `nao_reportado` três formas de não reportar
nível: valor vazio/`*`/`-%`, OID não implementado, e código especial da
RFC 3805 (nível negativo ou capacidade `<= 0`). **Não se sabe qual delas
as impressoras do parque realmente produzem** — a terceira é a mais
provável, e é justamente a que a primeira implementação classificava
errado.

Procedimento:

1. Identifique uma impressora com cartucho paralelo instalado e outra
   com original, de preferência do mesmo modelo.
2. Rode `--dry-run` nas duas e compare linha a linha.
3. Anote, para o paralelo: o `nivel_bruto`, a `capacidade_max` e o
   `status` atribuído.
4. Confirme que o original sai com status `ok` e percentual coerente.

O que se espera: paralelo com `status=nao_reportado` e original com
`status=ok`.

**Adaptações previstas conforme o resultado:**

- Se o paralelo devolver um valor **numérico fixo** (sempre 0%, sempre
  100%), nenhuma das três regras o pega — ele passaria como `ok` e o
  relatório mostraria um nível que não existe. Seria preciso outra
  estratégia de detecção, provavelmente comparando a estabilidade do
  nível ao longo de várias coletas.
- Se aparecer um marcador de texto ainda não previsto, basta acrescentá-lo
  a `_MARCADORES_SEM_MEDICAO` em `readings/service.py`.
- Se o paralelo reportar nível corretamente, a classificação continua
  valendo e ganha-se a confirmação de que o parque não tem esse problema.

**Teste a acrescentar depois:** com o comportamento real conhecido, criar
um teste em `tests/test_readings_service.py` reproduzindo exatamente os
valores observados no parque.

### 4B.3 — Descobrir o OID do contador de cópias

```bash
python descobrir_oids_canon.py --ip <IP> --apenas-numericos
```

Comparar com os contadores do painel e gravar o OID encontrado em
`SNMP_OID_CONTADOR_COPIAS`, no `.env` do servidor. Procedimento completo
em [COLETA_SNMP.md](COLETA_SNMP.md#6-como-descobrir-o-oid-de-cópias).

Confirmar também que `prtMarkerLifeCount` bate com o contador de **total**
do painel.

Se nenhum OID bater, o relatório trabalha só com o total — que já
responde a pergunta principal. A separação impressão/cópia é refinamento.

**Resultado (10.165.22.54, Canon iR1643i II):**

| Contador | OID | SNMP | Painel |
|---|---|---|---|
| `prtMarkerLifeCount` | `1.3.6.1.2.1.43.10.2.1.4.1.1` | 2913 | não bate |
| Canon 101 (total) | `1.3.6.1.4.1.1602.1.11.1.3.1.4.101` | 2926 | bate |
| Canon 201 (cópias) | `1.3.6.1.4.1.1602.1.11.1.3.1.4.201` | 42 | bate |

O `prtMarkerLifeCount` fica 13 páginas abaixo do total do painel, lido no
mesmo instante. Decisão: o total passa a vir do contador Canon 101, pela
nova variável `SNMP_OID_CONTADOR_TOTAL`; as cópias, do Canon 201.

A validação expôs um bug: os OIDs do `.env` eram consultados por walk, que
a partir de um OID completo nunca devolve o próprio valor — o contador de
cópias sairia sempre `NULL`. Passaram a ser lidos por get, com teste de
regressão em `tests/test_snmp_client.py`.

### 4B.4 — Levantar a cara real do parque

Com uma coleta completa gravada, verificar:

- Quantos suprimentos cada impressora expõe e quais tipos aparecem
  (`tipo_suprimento`: 3=toner, 4=resíduo, 9=tambor).
- Se `marca`/`modelo` foram deduzidos corretamente do `sysDescr`; se a
  heurística falhar, ajustar `deduzir_marca` em `collector/snmp_client.py`.
- Se alguma impressora devolve índice numa tabela e não na outra (linhas
  com `status='erro'`), o que indicaria MIB inconsistente naquele modelo.
- A taxa de `sem_resposta` por impressora — é o dado que decide se vale
  implementar retry na Fase 6.

**Resultado da primeira coleta completa (coleta #1, 2026-09-25, 66 impressoras):**

- **Suprimentos:** todas as 63 que responderam expõem **um único**
  suprimento, `Canon Toner T06 Black`, com `tipo_suprimento=21`
  (tonerCartridge) — nenhuma com 3, nem caixa de resíduo ou tambor.
  **Bug encontrado:** `eh_tipo_toner` só aceitava o 3, então a detecção de
  troca não rodaria para nenhuma impressora do parque. Passou a aceitar o
  21, com teste em `test_collector_service.py`.
- **Marca/modelo:** 62 Canon iR1643i II deduzidas corretamente. Uma
  (10.165.20.82) ficou com modelo e sem marca: o `sysDescr` não chegou
  naquela coleta, embora responda `Canon iR1643i II /P` fora dela. A
  heurística está certa; foi uma consulta que falhou isolada — ver
  "Pontos em aberto" abaixo.
- **MIB inconsistente:** nenhuma linha com `status='erro'`.
- **Toner paralelo:** 5 impressoras com `nao_reportado`, todas com o mesmo
  padrão da 4B.2 (`-2/100`).
- **Contadores:** todas as 63 com `paginas_total` e `paginas_copias`
  preenchidos.
- **`sem_resposta`:** 3 de 66 (4,5%) — 10.165.23.175, 10.165.21.45 e
  10.165.22.47. **Confirmado no local: estavam desligadas.** Não
  respondiam nem a ping, então retry não teria recuperado nenhuma. Uma
  coleta só não basta para a decisão da Fase 6, mas até aqui não há falha
  transitória no nível da impressora inteira.

### 4B.5 — Resposta parcial: tratada

Na 10.165.20.82 uma consulta (`sysDescr`) voltou em branco e as outras
não; a impressora contou como sucesso e o campo ficou `NULL` sem registro
do motivo. O mesmo podia acontecer com nível ou contadores.

Tratamento em `collector/snmp_client.py`:

- **Em branco** é consulta vazia **com erro** (timeout). Vazia sem erro é
  o agente dizendo que não implementa o OID, e continua valendo como
  ausência legítima — inclusive `noSuchName` no get dos contadores.
- Se a impressora respondeu a alguma consulta, as que voltaram em branco
  são **repetidas uma vez**. Não contraria a decisão da Fase 6: aquela é
  sobre impressora que não respondeu nada; aqui ela está comprovadamente
  no ar.
- Persistindo em branco uma consulta **essencial** — nível, capacidade,
  contador total ou de cópias —, a impressora vira **falha**
  (`sem_resposta`, com `resposta parcial, sem ...` no `error_summary`) em
  vez de gravar leitura errada. Contador entra na lista porque uma troca
  detectada numa leitura sem contador perde o rendimento deste cartucho e
  do próximo, sem volta; como falha, a próxima coleta detecta a troca
  com os contadores.
- Consulta **não essencial** em branco (série, `sysDescr`, nome,
  descrição, tipo) segue como `NULL`, mas a impressora aparece no
  `error_summary` como `resposta parcial, em branco: ...` sem contar como
  falha. O `--dry-run` mostra a linha `EM BRANCO`.

---

## Tempo de coleta: resolvido

A coleta #1 levou **9min50s** para 66 impressoras (15 em paralelo). Uma
impressora sozinha leva de 10 a 22 s.

### Diagnóstico (feito)

O `walk_cmd` da pysnmp usa `lexicographicMode=True` por padrão: o walk
**não para ao sair da tabela**, segue até o fim da MIB do agente. O
`_walk_oid` descarta as linhas de fora com `continue`, mas já pagou por
elas. Medido na 10.165.22.54:

| Tabela | `lexicographicMode=True` | `lexicographicMode=False` |
|---|---|---|
| `sysDescr` | 1246 linhas, 2,25 s | 1 linha, 0,02 s |
| `prtMarkerSuppliesLevel` | 897 linhas, 1,69 s | 1 linha, 0,02 s |
| `prtMarkerLifeCount` | 916 linhas, 1,71 s | 1 linha, 0,02 s |

Em todos os casos só **uma** linha era da tabela. São ~8 walks por
impressora, ~8 mil requisições SNMP para aproveitar 8 — e, com 15
impressoras ao mesmo tempo, é também a causa provável das respostas
parciais: quanto mais pacotes, mais timeouts.

### Passos (executados)

1. **Linha de base.** Guardar a saída do `--dry-run` de um conjunto de
   referência — a .54 (original), a .45 (paralelo) e a .20.82 (a da
   resposta parcial) — para comparar depois.
2. **Correção.** Passar `lexicographicMode=False` no `walk_cmd` de
   `_walk_oid`. Aproveitar para exigir o ponto no filtro de prefixo
   (`base_oid + "."`): hoje a base `...4.1` também casaria com `...4.101`.
3. **Resultado idêntico.** Repetir o `--dry-run` do conjunto de
   referência e comparar: mesmos suprimentos, níveis, tipos e contadores.
   Qualquer diferença bloqueia o passo seguinte.
4. **Coleta completa medida.** Rodar a coleta das 66 e registrar: duração
   total, e quantas impressoras tiveram `resposta parcial` no
   `error_summary`. Meta: abaixo de 1 minuto e nenhuma resposta parcial.
5. **Só se a meta não for atingida:** medir a duração por impressora (log
   em `_coletar_impressora`) e avaliar, nesta ordem, subir
   `COLLECTOR_MAX_CONCURRENT_REQUESTS`, reaproveitar um `SnmpEngine` por
   impressora em vez de um por consulta, e ajustar `SNMP_TIMEOUT_SECONDS`.
6. **Registrar aqui** os números antes/depois.

### Resultado

| Coleta | Mudança | Duração | Respostas parciais |
|---|---|---|---|
| #1 | — | 9min50s | 1 (10.165.20.82) |
| #2 | `lexicographicMode=False` | 1min16s | 0 |
| #3 | + um `SnmpEngine` por impressora | **15 s** | 0 |

O passo 3 deu saída **idêntica** nas três impressoras de referência depois
de cada mudança, e as três coletas gravaram os mesmos números (58 `ok`,
5 `nao_reportado`, 3 `sem_resposta`, nenhum contador nulo, nenhuma troca
falsa).

A coleta #2 não bateu a meta, então o passo 5 rodou. Medida por
impressora, uma consulta isolada levava 1,1 s, mas em paralelo a
primeira leva de 15 terminava junta depois de 67 s: o event loop estava
travado. A causa era o `SnmpEngine`: criar um custa ~0,09 s de CPU
**síncrona**, e com um por consulta eram ~600 na coleta. Passou a ser um
por impressora, compartilhado pelas consultas dela. Não foi preciso mexer
em paralelismo nem em timeout.

Dos 15 s restantes, a maior parte são as 3 impressoras desligadas
esperando o timeout (`SNMP_TIMEOUT_SECONDS=3` × 2 tentativas).

---

## Fase 5B — Teste de integração com o parque

**Bloqueada até haver acesso ao parque.** Complementa a Fase 5: onde os
testes atuais dublam a rede, este exercita a coisa real.

Escopo:

1. **Coleta completa de ponta a ponta**, contra um banco de testes
   separado (nunca o de produção), verificando que as 66 impressoras são
   consultadas, que `coleta_executada` fecha com status coerente e que a
   quantidade de linhas em `leitura_toner` bate com o esperado.
2. **Tempo total da coleta**, para confirmar que
   `COLLECTOR_MAX_CONCURRENT_REQUESTS=15` é adequado e que a coleta cabe
   folgadamente dentro do intervalo entre horários.
3. **Coleta sobreposta na prática**: disparar `cli.py` durante uma coleta
   agendada e confirmar que a segunda é recusada sem criar registro.
4. **Recuperação de coleta travada**: matar o processo no meio de uma
   coleta e confirmar que a rodada seguinte libera o `RUNNING` órfão após
   o timeout.
5. **Ciclo de troca real**: trocar um toner numa impressora, rodar a
   coleta e confirmar que a troca é detectada com `paginas_rendidas`
   coerente com o número anotado na caixa. É a validação final do
   objetivo do projeto.

Esses testes ficam separados dos da Fase 5 (sugestão: `tests/integracao/`,
fora do `testpaths` padrão), para que a suíte do dia a dia continue
rodando em qualquer máquina.

---

## Fase 6 — decisão pendente: como o processo roda no servidor

O servidor de produção é uma **máquina desktop com um pouco mais de RAM**,
não um servidor dedicado. Isso pesa contra soluções que exigem um
processo vigiado 24h ou uma camada de infraestrutura pesada.

### Opção A — Agendador de Tarefas do Windows chamando `cli.py`

Três tarefas (09h, 13h, 17h) executando `python cli.py --agendada`.

- **A favor:** nenhum processo vivo entre as coletas, consumo zero de RAM
  em repouso; o Windows cuida de iniciar após reboot; não há processo
  para morrer silenciosamente; a opção "executar assim que possível após
  perder o horário" cobre a máquina desligada; `cli.py` já sai com código
  1 em falha, então o painel do Windows mostra a rodada como falha.
- **Contra:** os horários passam a viver no Windows, e não no `.env`;
  três tarefas para manter; logs ficam onde o redirecionamento mandar, e
  não centralizados.
- Torna o APScheduler desnecessário.

### Opção B — `scheduler.py` como Serviço do Windows

Instalado com NSSM ou `pywin32`.

- **A favor:** horários e configuração centralizados no `.env`; um
  processo só; logs num lugar só; inicia com o boot mesmo sem usuário
  logado; o serviço reinicia sozinho se cair.
- **Contra:** depende de ferramenta externa (NSSM) para instalar; mantém
  um processo Python vivo 24h (dezenas de MB); se ele travar sem morrer,
  o Windows não percebe.

### Opção C — `scheduler.py` na pasta Inicializar

- **A favor:** setup trivial, sem ferramenta externa.
- **Contra:** depende de haver um usuário logado; a janela pode ser
  fechada por engano; não reinicia sozinho. **Frágil demais para o uso
  contínuo que este sistema exige.**

### Opção D — Container Docker com política de restart

- **A favor:** isolamento e reinício automático.
- **Contra:** o Docker Desktop no Windows consome RAM significativa antes
  mesmo de subir o container. **Desproporcional para um desktop
  improvisado** cujo gargalo é justamente memória.

### Recomendação

**Opção A**, pelo contexto. Num servidor improvisado, o modo de falha mais
provável é ninguém perceber que algo parou — e a Opção A é a única em que
não existe processo para parar: entre uma coleta e outra, nada roda. O
Windows já resolve boot, reagendamento e registro de falha.

A Opção B é a escolha certa se, mais adiante, a centralização de
configuração e logs passar a valer o processo permanente — por exemplo
quando a API da Fase 7 entrar e já houver um processo vivo de qualquer
forma. O `scheduler.py` fica pronto para isso.

**Passos após a decisão:**

- Se A: documentar a criação das três tarefas no Windows, com o diretório
  de trabalho correto (`backend/`) e o redirecionamento de log; marcar o
  APScheduler como dependência opcional no `requirements.txt`.
- Se B: documentar a instalação do serviço e a configuração de restart.

---

## Fase 7 — API HTTP

Dependências novas: `fastapi`, `uvicorn`.

Endpoints já antecipados pelas docstrings do código existente:

| Endpoint | Fonte de dados |
|---|---|
| `GET /health` | `database.check_connection()` |
| `GET /printers` | `readings.service.ultimas_leituras_por_impressora()` |
| `GET /printers/{id}/historico` | `readings.service.historico_por_impressora()` |
| `GET /collections/latest` | `collection_runs.service.obter_ultima_coleta()` |
| `GET /collections` | `collection_runs.service.listar_coletas_recentes()` |
| `GET /trocas` | `toner_changes.service.trocas_no_periodo()` |
| `POST /collections` | `collector.service.executar_coleta(MANUAL)` |

Manter o padrão de camadas: os routers só traduzem HTTP e chamam services.
Schemas Pydantic ficam separados das entidades — entidade é o formato do
banco, schema é o contrato da API, e eles podem divergir.

`POST /collections` precisa tratar `ColetaEmAndamento` e responder
**409 Conflict**, em vez de deixar virar erro 500.

---

## Fase 8 — Relatórios e apresentação

**Stack ainda não definida.** É a maior indefinição do projeto. Opções:

- **Frontend web** (React/Vue consumindo a API): mais trabalho, mais controle.
- **Relatório gerado** (Excel/PDF por período): é o formato que mais se
  parece com o que hoje é feito à mão, e pode ser entregue sem frontend.
- **Dashboard pronto** (Grafana/Metabase apontando para o MariaDB):
  entrega mais rápida, quase sem código, mas limita a customização e
  acrescenta um serviço a rodar no mesmo desktop.

Relatórios que o modelo de dados já sustenta assim que houver histórico:

- Status atual de todas as impressoras (nível por suprimento)
- Impressoras abaixo de um limiar de toner
- Cartuchos trocados por período, por impressora e por setor
- Páginas rendidas por cartucho — o número anotado à mão hoje
- Impressoras com cartucho paralelo (status `nao_reportado`)
- Saúde da coleta: taxa de `sem_resposta` por impressora

---

## Fora de fase

- **Alertas de toner baixo** (e-mail/Teams) — ainda não definido se entra
  no escopo.
- **Ambiente virtual e deploy** — o projeto não roda na máquina de
  desenvolvimento atual; o core é produzido aqui e executado no servidor.
