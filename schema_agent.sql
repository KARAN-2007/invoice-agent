-- Tables used by the agent and the human approval workflow.
-- Separate from schema.sql so the two files never clash.

-- Every step the agent takes: tool calls, results, retries, errors
CREATE TABLE IF NOT EXISTS agent_log (
    id SERIAL PRIMARY KEY,
    logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    step VARCHAR(100) NOT NULL,
    detail JSONB
);

-- One row per invoice the agent has checked. A human approves or rejects it here.
CREATE TABLE IF NOT EXISTS approval_requests (
    id SERIAL PRIMARY KEY,
    file_name VARCHAR(255) NOT NULL,
    recommendation VARCHAR(30) NOT NULL,
    summary TEXT,
    review_reason TEXT,
    status VARCHAR(20) DEFAULT 'PENDING',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    decided_by VARCHAR(100),
    decided_at TIMESTAMP,
    decision_note TEXT
);


-- People who can log in to the app. Passwords are stored as salted hashes, never as plain text.
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    full_name VARCHAR(100),
    role VARCHAR(20) NOT NULL CHECK (role IN ('vendor', 'approver', 'admin')),
    password_hash TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);