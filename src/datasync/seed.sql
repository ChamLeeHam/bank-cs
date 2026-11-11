-- =====================================================================
-- Demo data for lioncity_bank. ALL names, numbers and records are fictional.
-- Dates are relative to NOW(), so "yesterday" always works when you reload
-- this file before a demo.
--
-- Demo scenarios built in:
--   C001 two cards           -> "freeze my card" must ask which one (ask_back)
--   C001 two TTs this week   -> "did my transfer to mum arrive?" must ask which one
--   C001 payee 妈妈           -> entity linking from a Chinese nickname
--   C002 returned FAST       -> transfer-status explanation in English
--   C002 odd overseas charge -> fraud report -> urgent handoff
--   C003 card already frozen -> "freeze my credit card" should say it is already frozen
-- =====================================================================

USE lioncity_bank;

-- 客户 -----------------------------------------------------------------
INSERT INTO customers (customer_id, name_en, name_zh, phone_masked, email, preferred_lang, segment) VALUES
('C001', 'Tan Wei Ming', '陈伟明', '+65 9***-4821', 'weiming.tan@example.com', 'mixed', 'retail'),
('C002', 'Priya Nair',   NULL,     '+65 8***-1194', 'priya.nair@example.com',  'en',    'premier'),
('C003', 'Lim Mei Ling', '林美玲', '+65 9***-6602', 'meiling.lim@example.com', 'zh',    'retail');

-- 账户 -----------------------------------------------------------------
INSERT INTO accounts (account_id, customer_id, account_type, currency, balance, status, opened_at) VALUES
('SA-1001-0001', 'C001', 'savings',        'SGD', 12450.80, 'active', '2021-03-15'),
('MC-1001-0002', 'C001', 'multi_currency', 'CNY', 8600.00,  'active', '2022-07-01'),
('SA-1002-0001', 'C002', 'savings',        'SGD', 3280.15,  'active', '2019-11-20'),
('FD-1002-0002', 'C002', 'fixed_deposit',  'SGD', 50000.00, 'active', '2026-05-01'),
('SA-1003-0001', 'C003', 'savings',        'SGD', 920.40,   'active', '2023-01-09'),
('MC-1003-0002', 'C003', 'multi_currency', 'MYR', 1500.00,  'active', '2023-01-09');

-- 银行卡 ---------------------------------------------------------------
INSERT INTO cards (customer_id, account_id, last4, card_type, network, status, expiry, frozen_at) VALUES
('C001', 'SA-1001-0001', '4821', 'debit',  'Visa',       'active', '08/28', NULL),
('C001', NULL,           '7730', 'credit', 'Mastercard', 'active', '02/29', NULL),
('C002', 'SA-1002-0001', '1194', 'debit',  'Visa',       'active', '11/27', NULL),
('C003', 'SA-1003-0001', '6602', 'debit',  'UnionPay',   'active', '05/28', NULL),
('C003', NULL,           '3017', 'credit', 'Visa',       'frozen', '09/27', NOW() - INTERVAL 10 DAY);

-- 收款人 ---------------------------------------------------------------
INSERT INTO payees (payee_id, customer_id, nickname, full_name, bank_name, country, account_masked, currency) VALUES
(1, 'C001', '妈妈',      'Chen Meihua',   'Demo Bank of Fujian',      'CN', '6222 **** **** 3390', 'CNY'),
(2, 'C001', 'Jason',     'Jason Koh',     'Lion City Bank (Demo)',    'SG', '***-***-552-1',       'SGD'),
(3, 'C002', 'landlord',  'Lee Kok Wah',   'Merlion Savings (Demo)',   'SG', '***-***-870-4',       'SGD'),
(4, 'C002', 'mum',       'Lakshmi Nair',  'Kerala Demo Bank',         'IN', 'XXXX XXXX 7712',      'INR'),
(5, 'C003', '哥哥',      'Lim Wei Jie',   'Johor Demo Bank',          'MY', '1140 **** 2208',      'MYR'),
(6, 'C003', 'SP Group',  'SP Services',   'Lion City Bank (Demo)',    'SG', 'GIRO-***-0091',       'SGD');

-- 转账 / 汇款 -----------------------------------------------------------
INSERT INTO transfers (transfer_id, customer_id, from_account_id, payee_id, transfer_type, amount, currency, fee,
                       status, status_note_en, status_note_zh, created_at, completed_at) VALUES
-- C001: two overseas TTs to 妈妈 this week (ambiguous on purpose)
('TT2611000101', 'C001', 'SA-1001-0001', 1, 'Overseas TT', 500.00,  'SGD', 10.00, 'processing',
 'With the intermediary bank. Usually credited within 1-3 working days.',
 '款项已到达中转行，通常 1–3 个工作日内入账。',
 NOW() - INTERVAL 1 DAY, NULL),
('TT2611000102', 'C001', 'SA-1001-0001', 1, 'Overseas TT', 1200.00, 'SGD', 10.00, 'completed',
 'Credited to the beneficiary account.', '已存入收款人账户。',
 NOW() - INTERVAL 4 DAY, NOW() - INTERVAL 2 DAY),
('PN2611000103', 'C001', 'SA-1001-0001', 2, 'PayNow',      45.50,   'SGD', 0.00,  'completed',
 'Completed instantly.', '已实时到账。',
 NOW() - INTERVAL 2 DAY, NOW() - INTERVAL 2 DAY),
-- C002
('FT2611000201', 'C002', 'SA-1002-0001', 3, 'FAST',        2100.00, 'SGD', 0.00,  'returned',
 'Returned by the receiving bank: account number does not match the name. Funds refunded.',
 '收款行退回：账号与户名不符，款项已退回。',
 NOW() - INTERVAL 3 DAY, NULL),
('TT2611000202', 'C002', 'SA-1002-0001', 4, 'Overseas TT', 800.00,  'SGD', 10.00, 'pending_review',
 'Under routine compliance review. No action needed unless we contact you.',
 '正在进行例行合规审核，如需补充资料我们会联系您。',
 NOW() - INTERVAL 2 DAY, NULL),
-- C003
('TT2611000301', 'C003', 'MC-1003-0002', 5, 'Overseas TT', 1000.00, 'MYR', 10.00, 'failed',
 'Failed: beneficiary bank code invalid. Funds returned to your account.',
 '失败：收款银行代码有误，款项已退回您的账户。',
 NOW() - INTERVAL 6 DAY, NULL),
('GR2611000302', 'C003', 'SA-1003-0001', 6, 'GIRO',        86.20,   'SGD', 0.00,  'completed',
 'Monthly GIRO deduction completed.', '每月 GIRO 扣款已完成。',
 NOW() - INTERVAL 5 DAY, NOW() - INTERVAL 5 DAY);

-- 卡片交易 -------------------------------------------------------------
-- card_id follows insert order above: 1=C001-4821, 2=C001-7730, 3=C002-1194, 4=C003-6602, 5=C003-3017
INSERT INTO card_transactions (txn_id, card_id, merchant, country, amount, currency, txn_time, status) VALUES
('CT26110001', 1, 'FairPrice Tampines',       'SG', 86.35,  'SGD', NOW() - INTERVAL 1 DAY,  'posted'),
('CT26110002', 2, 'Grab',                     'SG', 18.40,  'SGD', NOW() - INTERVAL 2 DAY,  'posted'),
('CT26110003', 3, 'Kopitiam Raffles Place',   'SG', 6.80,   'SGD', NOW() - INTERVAL 1 DAY,  'posted'),
('CT26110004', 3, 'ELECTRO-WORLD ONLINE LTD', 'GB', 749.99, 'GBP', NOW() - INTERVAL 5 HOUR, 'pending'),
('CT26110005', 3, 'ELECTRO-WORLD ONLINE LTD', 'GB', 749.99, 'GBP', NOW() - INTERVAL 4 HOUR, 'pending'),
('CT26110006', 4, 'Sheng Siong Bedok',        'SG', 42.10,  'SGD', NOW() - INTERVAL 3 DAY,  'posted');

-- 一张已关闭的示例工单（坐席工作台有数据可展示）----------------------------
INSERT INTO tickets (session_id, customer_id, intent, sub_intent, priority, status, summary, agent_reply, created_at) VALUES
('demo-seed-0001', 'C003', 'complaint', 'app_problem', 'normal', 'closed',
 '客户反映 App 转账页面多次闪退。',
 '您好，我们已将问题转交技术团队，最新版本 App 已修复，请更新后再试。',
 NOW() - INTERVAL 7 DAY);
