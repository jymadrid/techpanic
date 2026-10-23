"""预注册有限主张的离线验证；不是心理恐慌或交易盈利认证。"""

from __future__ import annotations
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
import numpy as np
import pandas as pd


def protocol_template():
    return {
        "schema": "study-v1",
        "claim": "contemporaneous_downside_liquidity_stress",
        "instrument": "sh000688",
        "registered_at": None,
        "registration_evidence_url": None,
        "calibration_end": None,
        "holdout_start": None,
        "outcome": {"definition": "", "source": "", "independent_of_pi_qvix_returns": False},
        "baseline_definition": "",
        "minimum_sessions": 500,
        "minimum_episodes": 30,
        "minimum_delta_ap": 0.05,
        "block_sessions": 20,
        "bootstrap_replicates": 1000,
        "seed": 1729,
        "false_positive_budget": 0.1,
        "note": "示例门槛须试点后在观察留出集前确定，不是通用科学标准",
    }


def freeze_protocol(protocol: dict) -> dict:
    content = json.dumps(
        protocol, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    frozen_at = datetime.now().astimezone().isoformat()
    envelope_hash = hashlib.sha256(content + frozen_at.encode()).hexdigest()
    return {
        "protocol": protocol,
        "envelope_sha256": envelope_hash,
        "sha256": hashlib.sha256(content).hexdigest(),
        "scope": "local_protocol_integrity; not external preregistration authentication",
        "frozen_at": frozen_at,
    }


def average_precision(target, score):
    target = np.asarray(target, dtype=int)
    score = np.asarray(score, dtype=float)
    if not len(target) or target.sum() == 0 or not np.isfinite(score).all():
        return None
    order = np.argsort(-score, kind="stable")
    y = target[order]
    s = score[order]
    ends = np.r_[np.flatnonzero(s[:-1] != s[1:]), len(s) - 1]
    tp = np.cumsum(y)[ends]
    precision = tp / (ends + 1)
    recall = tp / y.sum()
    return float(np.sum(np.diff(np.r_[0.0, recall]) * precision))


def evaluate_study(data: pd.DataFrame, frozen: dict) -> dict:
    p = frozen.get("protocol", {})
    if (
        p.get("schema") != "study-v1"
        or p.get("claim") != "contemporaneous_downside_liquidity_stress"
    ):
        return {"status": "failed", "claim": p.get("claim"), "reasons": ["研究协议版本/有限主张不受支持"], "scope": "input_validation"}
    expected = freeze_protocol(p)["sha256"]
    result = {
        "status": "insufficient_evidence",
        "claim": p.get("claim"),
        "protocol_sha256": expected,
        "scope": "provided_independent_target_agreement; not authenticated emotions or prediction",
        "reasons": [],
    }
    if frozen.get("sha256") != expected:
        raise ValueError("预注册协议哈希不匹配")
    required = {"date", "pi", "baseline", "target", "episode", "report_at", "available_at"}
    if not required.issubset(data):
        return dict(result, reasons=["缺少数据日期、分数、独立目标或时点可得字段"])
    if (
        p.get("outcome", {}).get("independent_of_pi_qvix_returns") is not True
        or not p.get("outcome", {}).get("source")
        or not p.get("outcome", {}).get("definition")
        or not p.get("baseline_definition")
    ):
        return dict(
            result, reasons=["独立目标/基线定义未预先声明，禁止使用 PI/QVIX/收益尾部作为真值"]
        )
    if not all(
        p.get(k)
        for k in ["registered_at", "registration_evidence_url", "calibration_end", "holdout_start"]
    ):
        return dict(result, reasons=["缺少留出之前的预注册证据和时间划分"])
    registration_url = urlsplit(str(p["registration_evidence_url"]))
    if registration_url.scheme not in {"https", "http"} or not registration_url.hostname:
        return dict(result, status="failed", reasons=["预注册出处须为 HTTP(S) URL；并不自动证明其真实性"])
    if not all(
        str(v).strip()
        for v in (
            p.get("instrument", ""),
            p["outcome"].get("source", ""),
            p["outcome"].get("definition", ""),
            p.get("baseline_definition", ""),
        )
    ):
        return dict(result, reasons=["对象、独立目标或基线定义为空白"])
    registered = datetime.fromisoformat(p["registered_at"].replace("Z", "+00:00"))
    if registered.tzinfo is None:
        raise ValueError("预注册时间须有时区")
    frozen_at = frozen.get("frozen_at")
    if not frozen_at:
        return dict(result, reasons=["没有协议实际冻结时间；不能通过填写旧时间冒充预注册"])
    frozen_time = datetime.fromisoformat(frozen_at.replace("Z", "+00:00"))
    envelope = (
        json.dumps(
            p, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        + frozen_at.encode()
    )
    if frozen.get("envelope_sha256") != hashlib.sha256(envelope).hexdigest():
        raise ValueError("协议冻结时间完整性校验失败")
    if frozen_time.tzinfo is None:
        raise ValueError("协议冻结时间须有时区")
    split = pd.Timestamp(p["holdout_start"])
    cal_end = pd.Timestamp(p["calibration_end"])
    market_tz = timezone(timedelta(hours=8))
    split_instant = split.to_pydatetime().replace(tzinfo=market_tz)
    if cal_end >= split or registered >= split_instant or frozen_time >= split_instant:
        return dict(result, status="failed", reasons=["预注册/校准必须早于留出开始，不能事后回填"])
    d = data.copy()
    d["date"] = pd.to_datetime(d["date"], errors="raise")
    if (
        d["date"].dt.tz is not None
        or d["date"].isna().any()
        or (d["date"] != d["date"].dt.normalize()).any()
    ):
        raise ValueError("研究 date 必须是一日一个的无时区市场日期，不能用日内行冒充会话")
    if d["date"].duplicated().any() or not d["date"].is_monotonic_increasing:
        raise ValueError("研究日期必须唯一升序")
    for col in ["pi", "baseline", "target"]:
        d[col] = pd.to_numeric(d[col], errors="raise")
    if (
        not np.isfinite(d[["pi", "baseline", "target"]]).all().all()
        or not d["target"].isin([0, 1]).all()
    ):
        raise ValueError("研究分数必须有限，目标必须为 0/1")
    for row in d.itertuples():
        available = datetime.fromisoformat(str(row.available_at).replace("Z", "+00:00"))
        report = datetime.fromisoformat(str(row.report_at).replace("Z", "+00:00"))
        if available.tzinfo is None or report.tzinfo is None:
            raise ValueError("研究可得/报告时间须有时区")
        final_close = row.date.to_pydatetime().replace(hour=15, tzinfo=market_tz)
        if available > report or report < final_close or available < final_close:
            return dict(result, status="failed", reasons=["输入在报告时间不可得或行情来自未来"])
    held = d.loc[d["date"] >= split]
    cal = d.loc[d["date"] <= cal_end]
    if any(
        datetime.fromisoformat(str(x).replace("Z", "+00:00")) >= split_instant
        for x in cal["available_at"]
    ):
        return dict(
            result, status="failed", reasons=["校准输入在留出开始之后才可得，禁止用其设阈值"]
        )
    positive_runs = (held["target"].eq(1) & held["target"].shift(fill_value=0).eq(0)).cumsum()
    positives = held.loc[held["target"].eq(1)].copy()
    positives["run"] = positive_runs.loc[positives.index]
    if (
        positives["episode"].isna().any()
        or (positives.groupby("run")["episode"].nunique() != 1).any()
        or (positives.groupby("episode")["run"].nunique() != 1).any()
    ):
        return dict(result, status="failed", reasons=["压力事件必须连续且不可拆分或复用事件编号"])
    episodes = positives["run"].nunique()
    min_sessions = int(p.get("minimum_sessions", 500))
    min_events = int(p.get("minimum_episodes", 30))
    block = int(p.get("block_sessions", 20))
    reps = int(p.get("bootstrap_replicates", 1000))
    minimum_delta = float(p.get("minimum_delta_ap", 0.05))
    if (
        min_sessions < 1
        or min_events < 1
        or block < 2
        or not 100 <= reps <= 10000
        or not np.isfinite(minimum_delta)
        or not 0 < minimum_delta <= 1
    ):
        raise ValueError("证据门槛/时间块/重复次数无效")
    result.update(
        holdout_sessions=len(held),
        episodes=int(episodes),
        prevalence=float(held["target"].mean()) if len(held) else None,
    )
    if (
        len(held) < max(min_sessions, 5 * block)
        or episodes < min_events
        or held["target"].nunique() < 2
        or len(cal) < block
    ):
        return dict(result, reasons=["独立事件、留出会话或校准样本不足"])
    # 每个阳性 episode 必须是连续观测；不能把同一压力事件拆成独立样本。
    positive = held[held["target"] == 1]
    if positive["episode"].isna().any():
        return dict(result, reasons=["压力事件编号缺失"])
    y = held["target"].to_numpy(int)
    a = held["pi"].to_numpy(float)
    b = held["baseline"].to_numpy(float)
    ap = average_precision(y, a)
    base = average_precision(y, b)
    delta = ap - base
    rng = np.random.default_rng(int(p.get("seed", 1729)))
    samples = []
    n = len(y)
    for _ in range(reps):
        starts = rng.integers(0, max(1, n - block + 1), size=(n + block - 1) // block)
        ix = np.concatenate([np.arange(s, min(n, s + block)) for s in starts])[:n]
        if y[ix].sum() == 0 or y[ix].sum() == len(ix):
            continue
        samples.append(average_precision(y[ix], a[ix]) - average_precision(y[ix], b[ix]))
    if len(samples) < max(50, reps // 2):
        return dict(result, reasons=["有效时间块重采样不足"])
    ci = np.quantile(samples, [0.025, 0.975]).tolist()
    budget = float(p.get("false_positive_budget", 0.1))
    if not 0 < budget < 1:
        raise ValueError("误报预算必须在 0 与 1 之间")
    negatives = cal.loc[cal["target"] == 0, "pi"].to_numpy()
    if not len(negatives):
        return dict(result, reasons=["校准期没有负样本，无法固定告警阈值"])
    threshold = float(np.quantile(negatives, 1 - budget))
    pred = a > threshold
    tp = int(((y == 1) & pred).sum())
    fp = int(((y == 0) & pred).sum())
    false_positive_rate = fp / int((y == 0).sum())
    passed = (
        delta >= float(p.get("minimum_delta_ap", 0.05))
        and ci[0] > 0
        and false_positive_rate <= budget
    )
    return dict(
        result,
        status="passed" if passed else "failed",
        reasons=[] if passed else ["留出增量或置信下界未达到预注册门槛"],
        metrics={
            "average_precision": ap,
            "baseline_ap": base,
            "random_ap": float(y.mean()),
            "delta_ap": delta,
            "paired_block_ci95": ci,
            "block_sessions": block,
            "valid_replicates": len(samples),
            "threshold_calibration_only": threshold,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / int(y.sum()),
            "false_alarms_per100_sessions": 100 * fp / n,
            "false_positive_rate": false_positive_rate,
            "false_positive_budget": budget,
        },
    )


def evaluate_files(data_path: Path, protocol_path: Path):
    frozen = json.loads(protocol_path.read_text(encoding="utf-8-sig"))
    result = evaluate_study(pd.read_csv(data_path), frozen)
    result["input_sha256"] = hashlib.sha256(data_path.read_bytes()).hexdigest()
    return result
