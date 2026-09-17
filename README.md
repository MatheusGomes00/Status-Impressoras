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
    teste_snmp.py             MVP original - consulta uma impressora
    descobrir_oids_canon.py   descoberta dos contadores Canon
    requirements.txt

    database/                 pool de conexões + scripts SQL
    printers/                 cadastro das impressoras
    readings/                 leituras de nível de toner
    collection_runs/          registro de cada execução de coleta
    toner_changes/            eventos de troca de cartucho
    collector/                cliente SNMP e orquestração da coleta

docs/
    PADROES.md                padrões a seguir ao alterar o código
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

---

## Validação pendente em hardware

Três coisas só podem ser confirmadas com uma impressora real à mão:

1. **Nível conferido contra o painel.** Rode `cli.py --dry-run` e compare.
   O MVP mostrava um valor divergente do painel; a causa provável era o
   pareamento por posição entre as tabelas de nível e de capacidade, hoje
   substituído por pareamento pela chave da linha na MIB.

2. **Contador de cópias.** A Printer-MIB padrão só expõe o total de
   páginas. O contador de cópias separado existe apenas na MIB privada da
   Canon. Rode `descobrir_oids_canon.py`, compare com o painel e preencha
   `OID_CANON_CONTADOR_COPIAS` em `collector/oids.py`. Até lá,
   `paginas_copias` fica nulo.

3. **Contador de total.** Confirmar que `prtMarkerLifeCount` bate com o
   total exibido no painel.

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
