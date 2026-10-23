# techpanic

Public-data **relative market-stress** observations, not an official index, psychological panic probability, or investment advice. Financial construct validity remains **insufficient evidence**.

Python 3.12+, no personal API key. Activate a virtual environment, install requirements, then run:

```sh
python -m pip install -r requirements.txt
python -m techpanic
python -m techpanic --offline --json
python -m techpanic audit --help
```

Each online run queries Eastmoney, Tencent and Sina; later valid dates win, ties EM > Tencent > Sina. Source history is not spliced. A uses an unverified third-party QVIX proxy, B prices; different constructs and dates must not be subtracted. Percentile tiers use neutral labels, not fear or mania.

Evidence archival is off by default. Explicit --archive-evidence stores local response entity bytes, SHA-256 and append-only observation vintages. Hashes prove local integrity, not market authenticity. --knowledge-as-of is read-only/offline and never falls back to current cache; --date is retrospective current-history calculation.

Optional local calendar/reference inputs remain provided-not-authenticated. Audit commands evaluate independent-reference agreement, source sensitivity and preregistered continuous holdout/block-bootstrap studies. No external material means unknown or insufficient, not financial certification.

[Chinese audit protocol](docs/AUDIT_PROTOCOL.md). No new providers/factors or trading-profit optimization. Runtime data and archives are not published. MIT licenses code, not upstream redistribution.
