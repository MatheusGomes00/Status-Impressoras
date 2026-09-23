-- ============================================================
-- Schema completo do monitoramento de impressoras.
--
-- Ordem de execucao:
--   1. scriptCriarTabelas.sql      (este arquivo)
--   2. scriptInserirImpressoras.sql
--
-- O nome do banco tem hifen, entao precisa de crases em todo
-- SQL manual. O mesmo nome esta em DB_NAME no .env.
-- ============================================================

CREATE SCHEMA IF NOT EXISTS `monitoramentoimpressoras-ue`
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE `monitoramentoimpressoras-ue`;

-- ============================================================
-- Tabela: impressora
-- Cadastro das impressoras monitoradas.
--
-- marca/modelo sao preenchidos pelo coletor (sysDescr e
-- prtGeneralPrinterName), nao pelo cadastro manual - por isso
-- nascem NULL. O parque hoje e homogeneo (Canon mono), mas
-- essas colunas sao o que permite absorver um modelo diferente
-- sem refatorar o relatorio depois.
-- ============================================================
CREATE TABLE impressora (
    id_impressora INT AUTO_INCREMENT PRIMARY KEY,
    patrimonio VARCHAR(10) NOT NULL UNIQUE,
    ip_address VARCHAR(45) NOT NULL UNIQUE,
    location VARCHAR(150) NOT NULL,
    numero_serie VARCHAR(50) NOT NULL UNIQUE,
    marca VARCHAR(50) NULL,
    modelo VARCHAR(100) NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_active (active)
);

-- ============================================================
-- Tabela: coleta_executada
-- Registro de cada execucao da rotina de coleta.
-- Precisa vir ANTES de leitura_toner, que a referencia.
--
-- COLETA SOBREPOSTA
-- -----------------
-- Duas coletas simultaneas competiriam pela mesma rede e gravariam
-- leituras concorrentes para os mesmos suprimentos. O cenario nao e
-- hipotetico: o agendador dispara as 09/13/17 e alguem pode rodar
-- cli.py na mao no mesmo minuto - sao dois processos distintos, entao
-- um controle em memoria nao resolveria.
--
-- A exclusividade e garantida pelo PROPRIO BANCO, e nao por um
-- "consulta antes de inserir" no codigo: entre a consulta e o INSERT
-- existe uma janela em que os dois processos passariam pela checagem.
--
-- Como funciona: a coluna gerada vale 1 enquanto o status e RUNNING e
-- NULL em qualquer outro status. Como o MariaDB nao considera NULLs
-- duplicados num indice UNIQUE, so pode existir UMA linha RUNNING por
-- vez. A segunda tentativa falha no INSERT com erro de chave duplicada
-- e nenhum registro parcial chega a ser criado.
-- ============================================================
CREATE TABLE coleta_executada (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    trigger_type ENUM('SCHEDULED', 'MANUAL') NOT NULL DEFAULT 'SCHEDULED',
    iniciado_em DATETIME NOT NULL,
    finalizado_em DATETIME NULL,
    status ENUM('RUNNING', 'COMPLETED', 'PARTIAL', 'FAILED')
        NOT NULL DEFAULT 'RUNNING',
    total_impressoras INT NOT NULL DEFAULT 0,
    impressoras_sucesso INT NOT NULL DEFAULT 0,
    impressora_falha INT NOT NULL DEFAULT 0,
    error_summary TEXT NULL,

    execucao_exclusiva TINYINT UNSIGNED
        AS (IF(status = 'RUNNING', 1, NULL)) VIRTUAL,

    CONSTRAINT uq_uma_coleta_em_execucao UNIQUE (execucao_exclusiva),

    INDEX idx_iniciado_em (iniciado_em)
);

-- ============================================================
-- Tabela: leitura_toner
-- Uma linha por suprimento por execucao de coleta.
--
-- Por que ha mais de uma linha por impressora mesmo sendo
-- mono: a tabela prtMarkerSupplies expoe tambem a caixa de
-- residuo e, em alguns modelos, o tambor. tipo_suprimento
-- distingue (3=toner, 4=caixa de residuo, conforme RFC 3805)
-- e e o que impede o relatorio de somar residuo como se fosse
-- toner.
--
-- indice_suprimento e o sufixo do OID da linha na MIB
-- (hrDeviceIndex.supplyIndex achatado), nao a posicao na
-- resposta - e o que garante que nivel e capacidade sejam
-- sempre do MESMO suprimento.
--
-- paginas_total/paginas_copias sao contadores da impressora
-- inteira, repetidos nas linhas de suprimento da mesma coleta
-- de proposito: evita um JOIN em todo relatorio de consumo.
-- Sao gravados uma vez por coleta pelo coletor, entao nao ha
-- risco de divergencia entre as linhas.
-- ============================================================
CREATE TABLE leitura_toner (
    id_leitura BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    id_impressora INT NOT NULL,
    id_coleta_executada BIGINT UNSIGNED NOT NULL,
    indice_suprimento SMALLINT UNSIGNED NOT NULL DEFAULT 1,
    descricao_suprimento VARCHAR(100) NULL,
    tipo_suprimento SMALLINT UNSIGNED NULL,
    nivel_percentual DECIMAL(5,2) NULL,
    nivel_bruto INT NULL,
    capacidade_max INT NULL,
    paginas_total BIGINT UNSIGNED NULL,
    paginas_copias BIGINT UNSIGNED NULL,
    status ENUM('ok', 'nao_reportado', 'erro', 'sem_resposta')
        NOT NULL DEFAULT 'ok',
    coletado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_leitura_impressora
        FOREIGN KEY (id_impressora)
        REFERENCES impressora(id_impressora),

    CONSTRAINT fk_leitura_coleta
        FOREIGN KEY (id_coleta_executada)
        REFERENCES coleta_executada(id),

    CONSTRAINT uq_leitura_suprimento_por_coleta
        UNIQUE (id_impressora, id_coleta_executada, indice_suprimento),

    INDEX idx_impressora_coletado (id_impressora, coletado_em),
    INDEX idx_coleta_executada (id_coleta_executada)
);

-- ============================================================
-- Tabela: troca_toner
-- Evento de troca de cartucho, detectado pelo coletor quando o
-- nivel de um suprimento SOBE entre duas leituras consecutivas.
--
-- Existe como tabela (e nao como consulta derivada) porque
-- congela o contador de paginas no momento da troca. E esse
-- congelamento que reproduz automaticamente o numero que hoje
-- e anotado a mao na caixa do toner: paginas_rendidas e quanto
-- o cartucho anterior imprimiu da troca passada ate esta.
-- ============================================================
CREATE TABLE troca_toner (
    id_troca BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    id_impressora INT NOT NULL,
    indice_suprimento SMALLINT UNSIGNED NOT NULL,
    id_leitura_antes BIGINT UNSIGNED NULL,
    id_leitura_depois BIGINT UNSIGNED NOT NULL,
    detectado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    nivel_antes DECIMAL(5,2) NULL,
    nivel_depois DECIMAL(5,2) NULL,
    paginas_no_evento BIGINT UNSIGNED NULL,
    paginas_rendidas BIGINT UNSIGNED NULL,
    copias_no_evento BIGINT UNSIGNED NULL,
    copias_rendidas BIGINT UNSIGNED NULL,

    CONSTRAINT fk_troca_impressora
        FOREIGN KEY (id_impressora)
        REFERENCES impressora(id_impressora),

    CONSTRAINT fk_troca_leitura_antes
        FOREIGN KEY (id_leitura_antes)
        REFERENCES leitura_toner(id_leitura),

    CONSTRAINT fk_troca_leitura_depois
        FOREIGN KEY (id_leitura_depois)
        REFERENCES leitura_toner(id_leitura),

    CONSTRAINT uq_troca_por_leitura
        UNIQUE (id_impressora, indice_suprimento, id_leitura_depois),

    INDEX idx_troca_impressora_data (id_impressora, detectado_em)
);
