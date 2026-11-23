-- Synthetic legacy v1 ledger fixture for the RS-P01 rehearsal (ADR-0031 decision 2).
--
-- Provenance: written for the rehearsal record docs/finding/<date>-01-p01-synthetic-rehearsal.md.
-- The table body is the v1 schema that shipped before ADR-0031 (seven event kinds, no SPLIT);
-- it is pinned as a literal so the migration keeps working on files a v1 build left behind.
-- The two rows are the first two rows of canonical-ledger.csv in this directory, expressed as
-- stored v1 rows, with a neutral account label and an invented instrument. The digest columns
-- hold opaque synthetic hex: no verification step recomputes them, and none is a real digest of
-- any operator data.
--
-- Usage in the rehearsal: executed once into a temp path with sqlite3.executescript, copied to a
-- *legacy copy*, and upgraded only on that copy. Nothing here is ever migrated in place.

CREATE TABLE IF NOT EXISTS ledger_events (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    account TEXT NOT NULL CHECK (length(account) > 0),
    external_id TEXT NOT NULL CHECK (length(external_id) > 0),
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    event_type TEXT NOT NULL CHECK (
        event_type IN ('OPENING', 'BUY', 'SELL', 'DEPOSIT', 'WITHDRAWAL', 'FEE', 'CASH_DIVIDEND')
    ),
    external_source TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    market TEXT CHECK (market IS NULL OR market = 'us_equity'),
    mic TEXT,
    symbol TEXT,
    currency TEXT NOT NULL CHECK (currency = 'USD'),
    cash_effect TEXT NOT NULL,
    quantity TEXT,
    price TEXT,
    fees TEXT NOT NULL,
    basis TEXT,
    basis_status TEXT NOT NULL CHECK (basis_status IN ('KNOWN', 'UNKNOWN')),
    reverses_external_id TEXT,
    source_digest TEXT NOT NULL CHECK (length(source_digest) = 64),
    content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
    UNIQUE (account, external_id),
    UNIQUE (account, sequence)
);

CREATE INDEX IF NOT EXISTS ledger_events_sequence ON ledger_events (account, sequence);

INSERT INTO ledger_events (
    account, external_id, sequence, event_type, external_source, parser_version,
    effective_at, received_at, recorded_at, market, mic, symbol, currency,
    cash_effect, quantity, price, fees, basis, basis_status, reverses_external_id,
    source_digest, content_digest
) VALUES (
    'main', 'evt-0001', 1, 'OPENING', 'synthetic-broker', 'portfolio-canonical-csv-v1',
    '2024-01-02T15:00:00+00:00', '2024-01-02T21:00:00+00:00', '2024-01-02T21:00:00+00:00',
    'us_equity', 'XNAS', 'PRIVATESYM', 'USD',
    '1000.00', '10', '100.00', '0', '1000.00', 'KNOWN', NULL,
    '0000000000000000000000000000000000000000000000000000000000000000',
    '1111111111111111111111111111111111111111111111111111111111111111'
);

INSERT INTO ledger_events (
    account, external_id, sequence, event_type, external_source, parser_version,
    effective_at, received_at, recorded_at, market, mic, symbol, currency,
    cash_effect, quantity, price, fees, basis, basis_status, reverses_external_id,
    source_digest, content_digest
) VALUES (
    'main', 'evt-0002', 2, 'BUY', 'synthetic-broker', 'portfolio-canonical-csv-v1',
    '2024-01-03T15:00:00+00:00', '2024-01-03T21:00:00+00:00', '2024-01-03T21:00:00+00:00',
    'us_equity', 'XNAS', 'PRIVATESYM', 'USD',
    '-605.00', '5', '120.00', '5.00', NULL, 'KNOWN', NULL,
    '0000000000000000000000000000000000000000000000000000000000000000',
    '2222222222222222222222222222222222222222222222222222222222222222'
);

PRAGMA user_version=1;
