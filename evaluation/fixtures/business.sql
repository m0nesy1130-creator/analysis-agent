-- 仅供评测的虚构数据。重复导入会恢复下列样例记录。
CREATE DATABASE IF NOT EXISTS analysis_agent_eval CHARACTER SET utf8mb4;
USE analysis_agent_eval;

CREATE TABLE IF NOT EXISTS posted_payments (
    payment_id VARCHAR(32) PRIMARY KEY,
    contract_id VARCHAR(32) NOT NULL,
    billing_period VARCHAR(16) NOT NULL,
    amount DECIMAL(12, 2) NOT NULL,
    currency CHAR(3) NOT NULL
);

INSERT INTO posted_payments VALUES
    ('P-2026-001', 'MT-2026-001', '2026', 3200.25, 'CNY'),
    ('P-2026-002', 'MT-2026-001', '2026', 1799.75, 'CNY'),
    ('P-2025-001', 'MT-2026-001', '2025', 400.00, 'CNY'),
    ('P-2026-003', 'MT-2026-002', '2026', 800.00, 'CNY')
AS sample ON DUPLICATE KEY UPDATE
    contract_id = sample.contract_id,
    billing_period = sample.billing_period,
    amount = sample.amount,
    currency = sample.currency;

CREATE TABLE IF NOT EXISTS service_tickets (
    ticket_id VARCHAR(32) PRIMARY KEY,
    symptom VARCHAR(32) NOT NULL,
    status VARCHAR(16) NOT NULL,
    minutes INT NOT NULL
);

INSERT INTO service_tickets VALUES
    ('T-001', 'path_missing', 'open', 15),
    ('T-002', 'path_missing', 'open', 20),
    ('T-003', 'path_missing', 'closed', 10),
    ('T-004', 'permission_denied', 'open', 25)
AS sample ON DUPLICATE KEY UPDATE
    symptom = sample.symptom,
    status = sample.status,
    minutes = sample.minutes;
