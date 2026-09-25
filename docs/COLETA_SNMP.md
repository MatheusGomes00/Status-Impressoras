# Coleta SNMP: como funciona e o que ainda falta descobrir

Este documento explica o que o coletor pergunta às impressoras, por que
pergunta dessa forma, e o que ainda depende de validação em hardware.

---

## 1. O caminho de uma coleta

```
scheduler.py  ou  cli.py
        │
        ▼
collector/service.py .......... abre a coleta, distribui as impressoras,
        │                       conta sucesso/falha, fecha a coleta
        ▼
collector/snmp_client.py ...... fala SNMP, devolve valores crus
        │
        ▼
readings/service.py ........... interpreta os valores crus
        │
        ▼
readings/repository.py ........ grava em leitura_toner
```

O `snmp_client` **não interpreta nada** e o `readings/service` **não fala
com a rede**. Essa separação é o que permite testar toda a regra de
interpretação sem precisar de uma impressora ligada.

As impressoras são consultadas em paralelo, limitadas por
`COLLECTOR_MAX_CONCURRENT_REQUESTS` (padrão 15). Uma impressora que
falha nunca derruba as outras.

---

## 2. O que é perguntado a cada impressora

Tudo abaixo, com exceção do contador de cópias, é da **Printer-MIB
padrão (RFC 3805)** e vale para qualquer fabricante. Os OIDs estão
reunidos em [collector/oids.py](../backend/collector/oids.py).

| O quê | OID | Vira |
|---|---|---|
| Número de série | `1.3.6.1.2.1.43.5.1.1.17` | conferência do cadastro |
| Descrição do sistema | `1.3.6.1.2.1.1.1` | `impressora.marca` |
| Nome do modelo | `1.3.6.1.2.1.43.5.1.1.16` | `impressora.modelo` |
| Tipo do suprimento | `1.3.6.1.2.1.43.11.1.1.5` | `leitura_toner.tipo_suprimento` |
| Descrição do suprimento | `1.3.6.1.2.1.43.11.1.1.6` | `leitura_toner.descricao_suprimento` |
| Capacidade máxima | `1.3.6.1.2.1.43.11.1.1.8` | `leitura_toner.capacidade_max` |
| Nível atual | `1.3.6.1.2.1.43.11.1.1.9` | `leitura_toner.nivel_bruto` |
| Total de páginas | `1.3.6.1.2.1.43.10.2.1.4` | `leitura_toner.paginas_total` |
| **Cópias** | **configurável** (`.env`) | `leitura_toner.paginas_copias` |

Todas as consultas usam **walk**, e não `get` em índice fixo — inclusive
o número de série. É uma lição do MVP: muitas Canon não respondem no
índice que a MIB padrão prevê.

---

## 3. Por que o pareamento é pelo índice do OID

As quatro tabelas de suprimento (tipo, descrição, capacidade, nível) são
indexadas pela **mesma chave**: `hrDeviceIndex.supplyIndex`. Em
`1.3.6.1.2.1.43.11.1.1.9.1.2`, o sufixo `1.2` identifica a linha.

O MVP descartava o OID e guardava só o valor, pareando as listas por
posição com `zip()`. Isso funciona **apenas** se as tabelas devolverem
exatamente os mesmos índices, na mesma ordem e quantidade.

Numa Canon mono o walk devolve mais de uma linha — toner, caixa de
resíduo e, em alguns modelos, o tambor. Quando uma tabela tem uma linha
que a outra não tem, o `zip()` divide o nível do toner pela capacidade da
caixa de resíduo: sai um percentual plausível, mas errado. Era a causa
provável do valor divergir do painel. E pior, `zip()` trunca em silêncio:
três níveis com duas capacidades faziam um suprimento sumir sem erro.

Hoje `_walk_oid` devolve `{sufixo: valor}` e o casamento é pela chave da
linha. Quando um índice existe numa tabela e não na outra, a leitura é
gravada com status `erro` em vez de desaparecer.

---

## 4. Toner paralelo

Cartuchos paralelos não reportam nível. Isso aparece de três formas, e as
três viram o status **`nao_reportado`**:

1. **Valor vazio ou marcador** — `""`, `*`, `-`, `-%`, `N/D`.
2. **OID não implementado** — a pysnmp devolve
   `"No Such Object currently exists at this OID"`.
3. **Código especial da RFC 3805** — nível negativo (`-1` outro, `-2`
   desconhecido, `-3` "resta alguma coisa") ou capacidade `<= 0`.

A terceira forma é a mais comum e foi corrigida depois da primeira
implementação: esses códigos eram classificados como `ok` com nível
nulo, o que deixava o paralelo indistinguível de um suprimento que
legitimamente não é medível, e fazia o relatório de "onde há paralelo"
não encontrar nada. O valor cru continua gravado (`nivel_bruto` guarda o
`-2`), então nada se perde para diagnóstico.

**Por que não é `erro`:** o cartucho não informar não é falha do sistema.
Contar como erro faria a taxa de falha da coleta medir qualidade de
consumível em vez de saúde da rede.

**Efeito colateral desejado:** leituras `nao_reportado` ficam de fora da
detecção de troca. Sem isso, um paralelo que voltasse a reportar nível
criaria uma troca fantasma.

> **Ainda não validado em hardware.** Tudo acima é o comportamento
> esperado pela RFC. Como o parque não é alcançável da máquina de
> desenvolvimento, ainda não se sabe qual das três formas as impressoras
> com paralelo realmente produzem. Ver a Fase 4B em
> [CRONOGRAMA.md](CRONOGRAMA.md).

---

## 5. Contadores de página: total e cópias

São os dois números anotados à mão na caixa do toner a cada troca. Fazem
parte do contexto de **toda** coleta: cada leitura grava o contador no
instante em que foi lida, e é isso que permite calcular o rendimento do
cartucho na troca seguinte.

### Onde cada número fica gravado

| Coluna | Conteúdo |
|---|---|
| `leitura_toner.paginas_total` | contador total no momento da leitura |
| `leitura_toner.paginas_copias` | contador de cópias no momento da leitura |
| `troca_toner.paginas_no_evento` | total congelado no momento da troca |
| `troca_toner.paginas_rendidas` | **total desde a troca anterior** |
| `troca_toner.copias_no_evento` | cópias congeladas no momento da troca |
| `troca_toner.copias_rendidas` | **cópias desde a troca anterior** |

`paginas_rendidas` e `copias_rendidas` são o número que hoje é escrito na
caixa. Ficam `NULL` na primeira troca de cada impressora (não há marco
anterior) e também se o contador regredir — reset de contador na
impressora, caso em que um número negativo no relatório seria pior que a
ausência do dado.

Os contadores são da impressora inteira, mas repetidos nas linhas de
suprimento da mesma coleta. É denormalização deliberada: evita um JOIN em
todo relatório de consumo, e como o coletor grava o mesmo valor nas três
linhas de uma vez, não há risco de divergirem.

### Total: contador Canon, configurável

`prtMarkerLifeCount` (`1.3.6.1.2.1.43.10.2.1.4`) é o total padrão da
Printer-MIB, mas **na Canon do parque ele não bate com o painel**. Na
Fase 4B.3, lidos no mesmo instante na iR1643i II 10.165.22.54, ele deu
2913 e o contador Canon 101 deu 2926, que é o valor do painel.

Por isso o total também é configurável, em `SNMP_OID_CONTADOR_TOTAL`.
Vazio, a coleta usa `prtMarkerLifeCount`. No parque, fica apontando para o
contador Canon 101:

```
SNMP_OID_CONTADOR_TOTAL=1.3.6.1.4.1.1602.1.11.1.3.1.4.101
```

Se o OID configurado não responder, `paginas_total` fica `NULL`: a coleta
**não** recai no `prtMarkerLifeCount`. As duas fontes divergem, e misturá-las
entre coletas faria `paginas_rendidas` somar ou perder a diferença.

### Cópias: contador Canon 201

**A Printer-MIB não separa cópia de impressão.** Só existe o total. O
contador de cópias existe apenas na MIB privada do fabricante — na Canon,
sob `1.3.6.1.4.1.1602` — e o OID varia por série do equipamento.

Por isso ele **não** é uma constante do código: mora na variável de
ambiente `SNMP_OID_CONTADOR_COPIAS`, lida por `config.py`. Assim o valor
descoberto em campo é preenchido no `.env` do servidor, sem alterar
código nem refazer deploy.

No parque (Canon iR1643i II), o OID confirmado contra o painel na Fase
4B.3 é o contador Canon 201:

```
SNMP_OID_CONTADOR_COPIAS=1.3.6.1.4.1.1602.1.11.1.3.1.4.201
```

Os dois OIDs do `.env` são lidos por **get**, não por walk: são OIDs
completos, com o índice da linha, e um walk a partir deles devolve o que
vem depois, nunca o próprio valor.

Enquanto estiver vazio, a coleta roda normalmente e `paginas_copias` fica
`NULL`. **Chutar um OID privado seria pior que não ter o dado**: a
impressora responderia um número plausível de outra coisa e o relatório
passaria a mentir em silêncio.

---

## 6. Como descobrir o OID de cópias

Precisa de uma impressora do parque acessível e do painel dela à mão.

```bash
cd backend
python descobrir_oids_canon.py --ip 10.165.20.133 --apenas-numericos
```

O script faz duas coisas:

1. Mostra o valor de `prtMarkerLifeCount` — confirme que bate com o
   contador de **total** do painel.
2. Varre a MIB privada da Canon e lista os OIDs com valor numérico.

No painel da impressora, anote os contadores de **total** e de **cópias**.
Procure na saída o OID cujo valor bate com o de cópias e grave no `.env`
do servidor:

```
SNMP_OID_CONTADOR_COPIAS=1.3.6.1.4.1.1602.1.11.1.3.1.4.201
```

Na Canon, os contadores ficam em `1.3.6.1.4.1.1602.1.11.1.3.1.4.<código>`
(e repetidos em `...1.11.1.4.1.4.<código>`), onde o código é o mesmo do
painel: 101 = total, 201 = cópias, 301 = impressões.

O OID precisa ser **completo, com o índice da linha**. A partir daí a
coleta passa a preencher `paginas_copias` sozinha.

Para varrer só um ramo, quando a MIB inteira devolver coisa demais:

```bash
python descobrir_oids_canon.py --ip 10.165.20.133 --oid 1.3.6.1.4.1.1602.1.11.1.3
```

### Se nenhum OID bater

Duas possibilidades: o modelo não expõe o contador de cópias via SNMP, ou
ele está num ramo fora do varrido. Nesse caso o relatório trabalha só com
o total — que já responde "quantas páginas o cartucho rendeu", a pergunta
principal. A separação entre impressão e cópia é refinamento.

---

## 7. Conferir o nível contra o painel

```bash
python cli.py --ip 10.165.20.133 --dry-run
```

Mostra o que **seria** gravado, sem tocar no banco: série, marca, modelo,
contadores e uma linha por suprimento com descrição, tipo, nível
percentual, valores crus e status.

É a forma de validar que a correção do pareamento resolveu a divergência
do MVP. Confira cada suprimento contra o painel. Se o número ainda
divergir depois de o pareamento estar correto, a hipótese seguinte é a
Canon reportar nível em escala grosseira — o valor SNMP seria o correto
"de máquina" e o painel um arredondamento. Dá para confirmar num cartucho
recém-trocado: deveria dar perto de 100%.
