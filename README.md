# Monitoramento de toner do parque de impressoras

Coleta, via SNMP, o nível de toner das impressoras da rede; armazena o
histórico em MariaDB; detecta automaticamente as trocas de cartucho e
serve de base para os relatórios de consumo.

O objetivo final é substituir o controle manual — hoje, a cada troca de
toner, o consumo de páginas é anotado à mão na caixa do cartucho.

**Parque atual:** 66 impressoras Canon monocromáticas, um tipo de
cartucho. O código evita depender disso: a coleta usa a Printer-MIB
padrão (RFC 3805) e identifica os suprimentos pelo tipo reportado pela
própria impressora, não por uma suposição sobre o modelo.

---

## Estrutura

```
backend/
    config.py                 configuração central (lê o .env)
    cli.py                    coleta manual, ponto de entrada principal
    scheduler.py              coletas automáticas às 09h/13h/17h
    teste_snmp.py             MVP original - consulta uma impressora
    descobrir_oids_canon.py   descoberta dos contadores Canon
    requirements.txt
    pytest.ini

    database/                 pool de conexões + scripts SQL
    printers/                 cadastro das impressoras
    readings/                 leituras de nível de toner
    collection_runs/          registro de cada execução de coleta
    toner_changes/            eventos de troca de cartucho
    collector/                cliente SNMP e orquestração da coleta
    tests/                    suíte automatizada (não usa o parque)

docs/
    PADROES.md                padrões a seguir ao alterar o código
    COLETA_SNMP.md            o que é perguntado às impressoras e por quê
    TESTES.md                 o que a suíte cobre e o que ficou de fora
    CRONOGRAMA.md             fases, estado atual e decisões em aberto
```

Cada domínio segue sempre a mesma divisão em três camadas
(`entities` / `repository` / `service`). Ver [docs/PADROES.md](docs/PADROES.md).

---

## Preparação do ambiente

### 1. Dependências

```bash
cd backend
pip install -r requirements.txt
```

### 2. Banco de dados

Os scripts estão em `backend/database/`, e a ordem importa:

```bash
mysql -h <host> -P <porta> -u <usuario> -p < database/scriptCriarTabelas.sql
mysql -h <host> -P <porta> -u <usuario> -p < database/scriptInserirImpressoras.sql
```

O primeiro cria o schema `monitoramentoimpressoras-ue` e as quatro
tabelas. O segundo popula o cadastro das 66 impressoras.

### 3. Configuração

```bash
cp .env.example .env
```

Preencha `.env` com os dados reais. Ele **nunca** deve ser versionado —
já está no `.gitignore`.

Confira se ficou tudo certo:

```bash
python config.py                    # valida o .env, sem expor a senha
python database/connection.py       # testa a conexão com o banco
```

---

## Uso

Todos os comandos rodam com `backend/` como diretório atual.

```bash
# Consulta uma impressora e mostra o que seria gravado, sem tocar no banco
python cli.py --ip 10.165.20.133 --dry-run

# Coleta completa, gravando no banco
python cli.py

# Coleta restrita a algumas impressoras
python cli.py --ip 10.165.20.133 --ip 10.165.20.203

# Resultado da última coleta
python cli.py --ultima
```

### Coletas automáticas

```bash
python scheduler.py          # fica em execução, coleta às 09h/13h/17h
python scheduler.py --agora  # dispara uma coleta na partida também
```

Roda todos os dias da semana. Os horários vêm de `SCHEDULER_HORARIOS`.

Duas coletas nunca rodam ao mesmo tempo: quem garante é um índice UNIQUE
em `coleta_executada`, então nem mesmo um `cli.py` disparado à mão
durante a coleta agendada consegue criar registros concorrentes — a
segunda tentativa é recusada sem gravar nada.

**Como esse processo vai rodar em produção ainda não está decidido** —
as opções (Agendador de Tarefas do Windows, serviço, container) estão
comparadas em [docs/CRONOGRAMA.md](docs/CRONOGRAMA.md#fase-6--decisão-pendente-como-o-processo-roda-no-servidor).

### Testes

A suíte não usa o parque nem o banco: a consulta SNMP e os repositories
são dublados. Roda em qualquer máquina, inclusive nas que não alcançam a
rede das impressoras.

Num clone novo, três passos:

```bash
cd backend
pip install -r requirements.txt   # de preferência num venv
cp .env.example .env              # não precisa editar para rodar os testes
pytest
```

Duas coisas que costumam confundir:

- **As dependências precisam estar instaladas mesmo sem banco nem rede.**
  Os testes importam os módulos do projeto, e esses módulos importam
  `aiomysql` e `pysnmp` no topo. Nenhuma conexão é aberta - mas sem os
  pacotes, o import falha.
- **O `.env` precisa existir.** `config.py` valida a configuração no
  momento da importação, então sem ele a suíte nem chega a rodar. Os
  valores do `.env.example` servem como estão; não precisam ser reais.

O mapa dos 72 testes, o que cada um protege e o que ficou **fora** da
cobertura estão em [docs/TESTES.md](docs/TESTES.md).

---

## Validação pendente em hardware

Quatro coisas só podem ser confirmadas com uma impressora real à mão.
O procedimento completo está na **Fase 4B** do
[cronograma](docs/CRONOGRAMA.md#fase-4b--validação-em-hardware).

1. **Nível conferido contra o painel.** Rode `cli.py --dry-run` e compare.
   O MVP mostrava um valor divergente do painel; a causa provável era o
   pareamento por posição entre as tabelas de nível e de capacidade, hoje
   substituído por pareamento pela chave da linha na MIB.

2. **Como o parque reage a um toner paralelo.** O código classifica três
   formas de "não reportou nível", mas ainda não se sabe qual delas as
   impressoras realmente produzem. Comparar uma impressora com paralelo
   e outra com original resolve.

3. **Contador de cópias.** Resolvido na iR1643i II: contador Canon 201,
   em `SNMP_OID_CONTADOR_COPIAS`.

4. **Contador de total.** Resolvido: `prtMarkerLifeCount` não bate com o
   painel na Canon; o total vem do contador Canon 101, em
   `SNMP_OID_CONTADOR_TOTAL`.

---

## Como funciona a detecção de troca

A cada coleta, o nível de cada suprimento é comparado com a última
leitura válida do mesmo suprimento. Quando o nível **sobe** mais que o
limiar configurado (`COLLECTOR_LIMIAR_TROCA_TONER_PP`, padrão 20 pontos
percentuais), o sistema registra uma troca em `troca_toner` e congela os
contadores de página naquele instante.

`paginas_rendidas` é a diferença entre o contador da troca atual e o da
anterior — exatamente o número anotado hoje à mão na caixa.

O limiar existe porque o nível reportado oscila alguns pontos para cima
sem que nada tenha sido trocado; sem ele, o relatório contaria dezenas de
trocas inexistentes por mês.
