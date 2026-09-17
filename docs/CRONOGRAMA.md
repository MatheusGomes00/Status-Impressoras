# Cronograma de implementação

Documento vivo. Atualizar o estado de cada fase conforme o trabalho avança.

**Última atualização:** 2026-09-16

| Fase | Entrega | Estado |
|------|---------|--------|
| 1 | Configuração e conexão com o banco | Concluída |
| 2 | Schema e domínios em camadas | Concluída |
| 3 | Interpretação das leituras SNMP | Concluída |
| 4 | Coletor SNMP + CLI | Concluída (pendente validação em hardware) |
| 5 | Testes automatizados | Não iniciada |
| 6 | Agendador | Não iniciada — decisões em aberto |
| 7 | API HTTP | Não iniciada |
| 8 | Relatórios e apresentação | Não iniciada — stack indefinida |

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

### Fase 4 — Coletor SNMP
`collector/` (OIDs, cliente SNMP, orquestração) e `cli.py`. Detecção de
troca de cartucho integrada ao fluxo de coleta.

**Pendências desta fase, que só se resolvem com hardware:**

1. Rodar `python cli.py --ip <IP> --dry-run` contra uma impressora real e
   conferir o nível contra o painel. É a validação de que a correção do
   pareamento por índice de OID resolveu a divergência do MVP.
2. Rodar `python descobrir_oids_canon.py --ip <IP> --apenas-numericos` e
   preencher `OID_CANON_CONTADOR_COPIAS` em `collector/oids.py`. Enquanto
   estiver `None`, `paginas_copias` fica nulo e o relatório só terá o total.
3. Confirmar que `prtMarkerLifeCount` bate com o contador de total do painel.

---

## Fase 5 — Testes automatizados

Escopo mínimo, focado nas funções puras (as que carregam regra e não
precisam de banco nem de rede):

- `readings/service.py`: `calcular_percentual`, `interpretar_leitura`,
  `montar_leituras_snmp`, `indice_do_sufixo`
- `toner_changes/service.py`: `houve_troca`, `_rendimento`
- `collection_runs/service.py`: `_decidir_status`
- `collector/snmp_client.py`: `deduzir_marca`

Adicionar `pytest` e `pytest-asyncio` ao `requirements.txt` e criar
`backend/tests/`. Um teste de integração do coletor, com o cliente SNMP
substituído por um dublê, cobre o fluxo sem depender do parque.

---

## Fase 6 — Agendador

Dependência nova: `APScheduler`.

### O que já está decidido
A lógica não muda: o agendador chama `collector.service.executar_coleta`
com `trigger_type=SCHEDULED`. É a mesma função do `cli.py`, então coleta
manual e agendada nunca divergem.

### Decisões em aberto

**Horários.** A proposta inicial é 09h / 13h / 17h. Custo não é argumento
contra: 66 impressoras × 3 coletas = ~200 leituras/dia, ~6 mil linhas/mês.
Irrelevante para o MariaDB.

O argumento a favor de 3x/dia é a precisão da detecção de troca — quanto
mais espaçada a coleta, maior a chance de um cartucho ser trocado e
consumir páginas antes da próxima leitura, o que distorce
`paginas_rendidas`. O argumento a favor de menos é simplicidade.

Recomendação: manter 3x/dia. É barato e é o que dá qualidade ao número
que hoje é anotado à mão.

**Dias da semana.** Hospital opera 7 dias, mas o volume de impressão de
fim de semana é menor. Duas opções razoáveis:
- 09/13/17 todos os dias (mais simples, sem exceção a manter)
- 09/13/17 em dias úteis + uma coleta diária no fim de semana

**Coleta sobreposta.** Se as 09h chegarem com a coleta anterior ainda
`RUNNING`, o que fazer? Sugestão: pular a nova e registrar o motivo —
duas coletas simultâneas competem pela mesma rede e produzem leituras
duplicadas para o mesmo suprimento.

**Retry de impressora sem resposta.** Reagendar uma segunda tentativa
alguns minutos depois, ou esperar o próximo horário? Uma máquina desligada
à noite vai falhar de qualquer jeito; retry resolve só falha transitória
de rede. Sugestão: começar sem retry e medir a taxa de `sem_resposta`
antes de adicionar complexidade.

**Processo.** O agendador precisa de um processo vivo. Definir se roda
como serviço do Windows, container, ou dentro do processo da API (Fase 7).

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

---

## Fase 8 — Relatórios e apresentação

**Stack ainda não definida.** É a maior indefinição do projeto. Opções:

- **Frontend web** (React/Vue consumindo a API): mais trabalho, mais controle.
- **Relatório gerado** (Excel/PDF por período): é o formato que mais se
  parece com o que hoje é feito à mão, e pode ser entregue sem frontend.
- **Dashboard pronto** (Grafana/Metabase apontando para o MariaDB):
  entrega mais rápida, quase sem código, mas limita a customização.

Relatórios que o modelo de dados já sustenta assim que houver histórico:

- Status atual de todas as impressoras (nível por suprimento)
- Impressoras abaixo de um limiar de toner
- Cartuchos trocados por período, por impressora e por setor
- Páginas rendidas por cartucho — o número anotado à mão hoje
- Impressoras com cartucho paralelo (status `nao_reportado`)
- Saúde da coleta: taxa de `sem_resposta` por impressora

---

## Fora de fase

- **Alertas de toner baixo** (e-mail/Teams) — ainda não definido se entra no escopo.
- **Ambiente virtual e deploy** — o projeto não roda na máquina de
  desenvolvimento atual; o core é produzido aqui e executado no servidor.
