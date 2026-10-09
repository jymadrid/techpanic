# techpanic — Tech-Sector Panic Index (PI) for China A-shares

> One command. Today's panic reading for China's tech sector. No API key, no account, no configuration.

[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**techpanic** compresses "how panicked is China's tech sector today?" into a single
0–100 number — the **PI (Panic Index)** — combining three academically-motivated
market features:

1. **Volatility surge (S)** — short-term realized vol rising relative to long-term
2. **Downside asymmetry (A)** — down-move vol exceeding up-move vol
3. **Forward fear (F)** — implied volatility from index options (QVIX)

and grading it against history: **Calm / Normal / Alert / Panic / Extreme Panic**.

> 🇨🇳 中文文档请看 [README.md](README.md)。本文件是英文单页概览。

---

## Quick start

### Zero-experience users (one double-click)

| OS | Do this |
|---|---|
| **Windows** | Download the repo, double-click `start.bat` |
| **macOS** | Double-click `start.command` (if blocked: right-click → Open) |
| **Linux** | `bash start.sh` |

The script creates a local virtualenv, installs dependencies, fetches public data,
prints the reading, and writes results to `data/output/`.
**No terminal knowledge required. No accounts. No keys. No config editing.**

### Command-line users

```bash
git clone https://github.com/jymadrid/techpanic.git
cd techpanic
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m techpanic
```

### Browser only

Open [`notebooks/quickstart_colab.ipynb`](notebooks/quickstart_colab.ipynb)
in Google Colab and hit "Run all".

---

## What you get

```
【科创50】最新交易日 2026-10-08

  A | 完整口径（三因子，含期权）  数据日 2026-09-30
      PI =  53.7  【常态】  历史分位 49%

  B | 即时口径（两因子，仅价格）  数据日 2026-10-08（含最新交易日）
      PI =  61.5  【警戒】  历史分位 75%
      当日 -4.82%  近5日 -12.31%  方向 向下
```

Plus five files in `data/output/`: `summary.md`,
`latest.json` (schema v1), `badge.json`, and one CSV per index
(UTF-8 BOM so Excel renders Chinese correctly).

---

## The two readings — the most important thing to understand

| | A (full) | B (price-only) |
|---|---|---|
| Factors | S + A + **implied volatility** | S + A only |
| Weights | 0.40 / 0.35 / 0.25 | (0.40 + 0.35) / 0.75 |
| Data date | QVIX is published after close; **usually lags 1 trading day** | Same day |
| Use | Research, citations, the official reading | Today's move |

> ⚠️ **The two readings must not be subtracted from each other.**
> The difference mixes "different factor set" with "different data date" and cannot
> be decomposed. To measure today's incremental shock, compare B against
> *yesterday's B*.

---

## Reading it correctly

PI measures **state**, not **direction**:

| PI | Direction | Interpretation |
|---|---|---|
| High | Down | **Panic** — falling, and volatility is amplifying the fear |
| High | Up | **Euphoria** — rising, but volatility is also expanding. Not panic |
| Low | — | Calm, regardless of direction |

PI is a thermometer, not a crystal ball. It does not forecast returns and is
**not investment advice**. See [DISCLAIMER.md](DISCLAIMER.md).

---

## Where the data comes from

**Entirely public, free interfaces — no account, no API key, no payment.**

| Data | Source | How | Measured |
|---|---|---|---|
| Index daily bars | Sina Finance | `akshare.stock_zh_index_daily()` | ~0.8 s, 1637 rows |
| QVIX implied vol | `1.optbbs.com` | Direct CSV wide-table download (914 KB) | **1.7 s – 123 s** |

### Three things you should know

1. **The first run needs the network.** This repository ships **no data at all**,
   by design (bundled data goes stale and is more misleading than nothing).
   With no network and no cache, the program exits with code 3 and clear guidance —
   it never pretends to succeed.
2. **The QVIX upstream is a single point of failure.** A small third-party host,
   HTTP-only, ~8 KB/s, highly variable latency. The program allows 150 s per attempt
   and a 300 s total budget, then **degrades to the local cache** and labels the
   staleness explicitly. That is by design, not a bug.
3. **Upstream column layout can change.** QVIX columns are hard-coded slices.
   Three defenses guard against silently reading the wrong column:
   column-count assertion, plausibility assertion, and **cross-validation against
   the local cache**.

---

## Commands

```bash
python -m techpanic                     # fetch + compute + print (default)
python -m techpanic --offline           # cache only, zero HTTP
python -m techpanic --refresh           # ignore cache TTL, force re-fetch
python -m techpanic --json              # machine-readable output to stdout
python -m techpanic --date 2026-09-30   # use data up to that date only
python -m techpanic --with-sse50        # also compute SSE 50 (validation only)
python -m techpanic --check             # environment / data-source self-test
```

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Success — both readings current |
| `2` | **Partial degradation** (QVIX lag, or cache used) — normal, not a failure |
| `3` | No data available (no network, no cache) |
| `4` | Environment problem (missing dependencies) |
| `5` | Bad arguments |
| `130` | Interrupted |

---

## Configuration

**You do not need to configure anything.** To change something:

```bash
cp config.toml.example data/config.toml
```

Precedence: **CLI > environment (`TECHPANIC_*`) > TOML > defaults.**
Full reference: [docs/CONFIGURATION.md](docs/CONFIGURATION.md) (Chinese).

---

## Provenance and validation

The algorithm and interpretation framework come from a Chinese quantitative
research document. Open-sourcing it involved three changes:

1. **Fixed a real defect.** The reference implementation's `ewm(span=3)`
   carries stale state across days where QVIX is missing, so its "full" reading
   showed a value whose data date did not match the row's date. This project sets
   those days to NaN explicitly.
   Scope: 14 of 739 trading days differ, max difference 1.0476, decaying by ≈1/3
   per day. Justification: this project's full reading can be recomputed exactly
   from its own S/A/F columns; the reference's cannot.
2. **Made degradation a first-class citizen.** Every fallback is labelled in the
   output. Silent degradation is explicitly forbidden.
3. **Made trust testable.** 45 tests, including causality tests (appending future
   data must not change historical readings) and four bad-data negative cases.

```
S/A/F and PI_price match the reference implementation bit-for-bit (max abs diff 0.0)
```

Details: [docs/VALIDATION.md](docs/VALIDATION.md), [docs/METHODOLOGY.md](docs/METHODOLOGY.md) (Chinese).

---

## License

[MIT](LICENSE) © 2026 techpanic contributors.

This repository contains **code only** — no data files, snapshots or mirrors.
All data is fetched at runtime from public interfaces.

## Dual-source index data

Each online run requests Eastmoney, Tencent, and Sina daily history. Use the valid source with the latest end date; break ties in the order Eastmoney > Tencent > Sina. Fall back to cached history only if all three sources fail validation. Tencent history is fetched in yearly segments, not replaced with a short rolling window. Index requests are not skipped by the QVIX cache TTL. Logs and JSON include source dates and selection reasons. A successful request does not guarantee current-day closing data.
