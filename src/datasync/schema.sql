-- =====================================================================
-- 狮城银行（演示）Lion City Bank (Demo) — mock core-banking schema
-- MySQL 8.x. All data in this database is fictional.
-- Re-running this file drops and recreates every table in lioncity_bank.
-- =====================================================================

CREATE DATABASE IF NOT EXISTS lioncity_bank
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE lioncity_bank;

SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS turn_log, tickets, card_transactions, transfers, payees, cards, accounts, customers,
                     product_alias, product_attr, product, product_category;
SET FOREIGN_KEY_CHECKS = 1;

-- 客户 -----------------------------------------------------------------
CREATE TABLE customers (
  customer_id     VARCHAR(10)  PRIMARY KEY            COMMENT '客户号，如 C001',
  name_en         VARCHAR(100) NOT NULL,
  name_zh         VARCHAR(50)  NULL,
  phone_masked    VARCHAR(20)  NOT NULL               COMMENT '仅存脱敏手机号，如 +65 9***-1234',
  email           VARCHAR(100) NULL,
  preferred_lang  ENUM('en','zh','mixed') NOT NULL DEFAULT 'en',
  segment         ENUM('retail','premier') NOT NULL DEFAULT 'retail',
  demo_otp        CHAR(6)      NOT NULL DEFAULT '123456' COMMENT '演示用固定 OTP，真实系统不可这样存储',
  created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
) COMMENT '客户';

-- 账户 -----------------------------------------------------------------
CREATE TABLE accounts (
  account_id      VARCHAR(20)  PRIMARY KEY,
  customer_id     VARCHAR(10)  NOT NULL,
  account_type    ENUM('savings','current','multi_currency','fixed_deposit') NOT NULL,
  currency        CHAR(3)      NOT NULL,
  balance         DECIMAL(15,2) NOT NULL DEFAULT 0,
  status          ENUM('active','dormant','closed') NOT NULL DEFAULT 'active',
  opened_at       DATE         NOT NULL,
  CONSTRAINT fk_acc_cust FOREIGN KEY (customer_id) REFERENCES customers(customer_id),
  INDEX idx_acc_cust (customer_id)
) COMMENT '账户（一个客户可有多个账户 / 多币种）';

-- 银行卡 ---------------------------------------------------------------
CREATE TABLE cards (
  card_id         INT AUTO_INCREMENT PRIMARY KEY,
  customer_id     VARCHAR(10)  NOT NULL,
  account_id      VARCHAR(20)  NULL                   COMMENT '借记卡关联的账户；信用卡为空',
  last4           CHAR(4)      NOT NULL               COMMENT '只存后四位',
  card_type       ENUM('debit','credit') NOT NULL,
  network         ENUM('Visa','Mastercard','UnionPay') NOT NULL,
  status          ENUM('active','frozen','cancelled','pending_activation') NOT NULL DEFAULT 'active',
  expiry          CHAR(5)      NOT NULL               COMMENT 'MM/YY',
  frozen_at       DATETIME     NULL,
  CONSTRAINT fk_card_cust FOREIGN KEY (customer_id) REFERENCES customers(customer_id),
  CONSTRAINT fk_card_acc  FOREIGN KEY (account_id)  REFERENCES accounts(account_id),
  UNIQUE KEY uk_card_cust_last4 (customer_id, last4)
) COMMENT '银行卡';

-- 收款人 ---------------------------------------------------------------
CREATE TABLE payees (
  payee_id        INT AUTO_INCREMENT PRIMARY KEY,
  customer_id     VARCHAR(10)  NOT NULL,
  nickname        VARCHAR(50)  NOT NULL               COMMENT '客户对收款人的称呼，如 妈妈 / landlord，用于实体链接',
  full_name       VARCHAR(100) NOT NULL,
  bank_name       VARCHAR(100) NOT NULL,
  country         CHAR(2)      NOT NULL               COMMENT 'ISO 国家代码',
  account_masked  VARCHAR(30)  NOT NULL,
  currency        CHAR(3)      NOT NULL,
  CONSTRAINT fk_payee_cust FOREIGN KEY (customer_id) REFERENCES customers(customer_id),
  INDEX idx_payee_cust (customer_id)
) COMMENT '已保存的收款人';

-- 转账 / 汇款 -----------------------------------------------------------
CREATE TABLE transfers (
  transfer_id     VARCHAR(20)  PRIMARY KEY            COMMENT '交易参考号',
  customer_id     VARCHAR(10)  NOT NULL,
  from_account_id VARCHAR(20)  NOT NULL,
  payee_id        INT          NOT NULL,
  transfer_type   ENUM('FAST','PayNow','GIRO','Overseas TT') NOT NULL,
  amount          DECIMAL(15,2) NOT NULL,
  currency        CHAR(3)      NOT NULL,
  fee             DECIMAL(10,2) NOT NULL DEFAULT 0,
  status          ENUM('completed','processing','pending_review','failed','returned') NOT NULL,
  status_note_en  VARCHAR(255) NULL,
  status_note_zh  VARCHAR(255) NULL,
  created_at      DATETIME     NOT NULL,
  completed_at    DATETIME     NULL,
  CONSTRAINT fk_tr_cust  FOREIGN KEY (customer_id)     REFERENCES customers(customer_id),
  CONSTRAINT fk_tr_acc   FOREIGN KEY (from_account_id) REFERENCES accounts(account_id),
  CONSTRAINT fk_tr_payee FOREIGN KEY (payee_id)        REFERENCES payees(payee_id),
  INDEX idx_tr_cust_time (customer_id, created_at)
) COMMENT '转账与汇款记录';

-- 卡片交易（用于盗刷 / 争议场景）---------------------------------------
CREATE TABLE card_transactions (
  txn_id          VARCHAR(20)  PRIMARY KEY,
  card_id         INT          NOT NULL,
  merchant        VARCHAR(100) NOT NULL,
  country         CHAR(2)      NOT NULL,
  amount          DECIMAL(15,2) NOT NULL,
  currency        CHAR(3)      NOT NULL,
  txn_time        DATETIME     NOT NULL,
  status          ENUM('posted','pending','reversed') NOT NULL DEFAULT 'posted',
  CONSTRAINT fk_txn_card FOREIGN KEY (card_id) REFERENCES cards(card_id),
  INDEX idx_txn_card_time (card_id, txn_time)
) COMMENT '卡片消费记录';

-- 转人工工单 -------------------------------------------------------------
CREATE TABLE tickets (
  ticket_id       INT AUTO_INCREMENT PRIMARY KEY,
  session_id      VARCHAR(64)  NOT NULL,
  customer_id     VARCHAR(10)  NOT NULL,
  intent          VARCHAR(50)  NOT NULL               COMMENT '一级意图',
  sub_intent      VARCHAR(60)  NULL                   COMMENT '二级意图，如 app_problem，方便坐席分类处理',
  priority        ENUM('normal','urgent') NOT NULL DEFAULT 'normal',
  status          ENUM('open','replied','closed') NOT NULL DEFAULT 'open',
  summary         TEXT         NOT NULL               COMMENT '对话摘要（已脱敏）',
  agent_reply     TEXT         NULL,
  created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_tk_cust FOREIGN KEY (customer_id) REFERENCES customers(customer_id),
  INDEX idx_tk_status (status, priority)
) COMMENT '转人工工单（n8n 读取并通知坐席）';

-- 对话审计日志（对应 GraphState 的 trace / tool_calls）-------------------
CREATE TABLE turn_log (
  log_id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  session_id      VARCHAR(64)  NOT NULL,
  customer_id     VARCHAR(10)  NOT NULL,
  question_masked TEXT         NOT NULL               COMMENT '只记录脱敏后的问题',
  lang            ENUM('en','zh','mixed') NOT NULL,
  intent          VARCHAR(50)  NULL                   COMMENT '一级意图',
  sub_intent      VARCHAR(60)  NULL                   COMMENT '二级意图（看板按二级意图统计）',
  intent_score    DECIMAL(4,3) NULL,
  router_backend  VARCHAR(20)  NULL,
  decision        VARCHAR(20)  NULL,
  route           VARCHAR(20)  NULL,
  trace           JSON         NULL                   COMMENT '节点执行路径',
  tool_calls      JSON         NULL                   COMMENT '工具名、参数、结果',
  answer          TEXT         NULL,
  latency_ms      INT          NULL,
  created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_log_session (session_id, created_at),
  INDEX idx_log_time (created_at)
) COMMENT '每轮对话的审计日志（每日报表数据来源）';
-- 产品分类 -------------------------------------------------------------
CREATE TABLE product_category (
  category_id   VARCHAR(20)  PRIMARY KEY            COMMENT '如 transfer',
  name_zh       VARCHAR(20)  NOT NULL,
  name_en       VARCHAR(30)  NOT NULL
) COMMENT '产品分类：转账、银行卡、账户';

-- 产品 -----------------------------------------------------------------
CREATE TABLE product (
  product_id    VARCHAR(30)  PRIMARY KEY            COMMENT '如 overseas_tt',
  category_id   VARCHAR(20)  NOT NULL,
  name_zh       VARCHAR(30)  NOT NULL,
  name_en       VARCHAR(50)  NOT NULL,
  CONSTRAINT fk_prod_cat FOREIGN KEY (category_id) REFERENCES product_category(category_id)
) COMMENT '银行产品';

-- 产品属性 -------------------------------------------------------------
CREATE TABLE product_attr (
  attr_id       INT AUTO_INCREMENT PRIMARY KEY,
  product_id    VARCHAR(30)  NOT NULL,
  attr_key      VARCHAR(30)  NOT NULL               COMMENT '如 fee、arrival_time、daily_limit',
  name_zh       VARCHAR(30)  NOT NULL,
  name_en       VARCHAR(50)  NOT NULL,
  value_zh      VARCHAR(200) NOT NULL,
  value_en      VARCHAR(200) NOT NULL,
  CONSTRAINT fk_attr_prod FOREIGN KEY (product_id) REFERENCES product(product_id),
  UNIQUE KEY uk_prod_attr (product_id, attr_key)
) COMMENT '产品属性，数值和知识库文档一致';

-- 产品别名 -------------------------------------------------------------
CREATE TABLE product_alias (
  alias_id      INT AUTO_INCREMENT PRIMARY KEY,
  product_id    VARCHAR(30)  NOT NULL,
  alias         VARCHAR(50)  NOT NULL               COMMENT '客户的说法，如 电汇、汇钱回家、wire transfer',
  CONSTRAINT fk_alias_prod FOREIGN KEY (product_id) REFERENCES product(product_id),
  UNIQUE KEY uk_alias (alias)
) COMMENT '客户说法 -> 产品，用来做实体链接';
