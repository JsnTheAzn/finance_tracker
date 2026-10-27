-- Finance Tracker tables. Safe to run repeatedly. See docs/phase5.md.

CREATE TABLE IF NOT EXISTS transactions (
    id CHAR(36) NOT NULL PRIMARY KEY,
    seq BIGINT NOT NULL AUTO_INCREMENT,
    amount BIGINT NOT NULL,
    description VARCHAR(512) NOT NULL,
    category VARCHAR(100) NOT NULL,
    date DATE NOT NULL,
    external_id VARCHAR(255) COLLATE utf8mb4_bin NULL,
    pending BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_transactions_seq (seq),
    UNIQUE KEY uq_transactions_external_id (external_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS plaid_items (
    item_id VARCHAR(255) COLLATE utf8mb4_bin NOT NULL PRIMARY KEY,
    institution_id VARCHAR(100) NOT NULL,
    access_token_encrypted TEXT NOT NULL,
    sync_cursor TEXT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
