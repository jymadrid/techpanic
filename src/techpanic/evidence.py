"""可选本地证据档案；哈希证明留存完整性，不证明行情真实性。"""

from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sqlite3
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4
import pandas as pd


def canonical(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def timestamp(value: str) -> str:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("证据时间必须包含时区")
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


_SECRET_KEYS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "token",
    "api_key",
    "apikey",
    "password",
    "access_token",
}


def public_metadata(url: str, params: dict, headers: dict) -> None:
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
    ):
        raise ValueError("证据 URL 必须是无凭据的 HTTP(S) 地址")
    for key in list(params) + list(headers) + [k for k, _ in parse_qsl(parts.query)]:
        if str(key).lower() in _SECRET_KEYS:
            raise ValueError("不允许将凭据字段存入证据档案")


class EvidenceStore:
    def __init__(self, root: Path, *, read_only: bool = False):
        self.root = Path(root)
        self.read_only = read_only
        if read_only:
            self.db = sqlite3.connect(
                (self.root / "observations.sqlite3").resolve().as_uri() + "?mode=ro", uri=True
            )
            return
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "objects").mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.root / "observations.sqlite3")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS observations(id TEXT PRIMARY KEY, kind TEXT, dataset TEXT, source TEXT, observed TEXT, digest TEXT, metadata TEXT, metadata_hash TEXT)"
        )
        for action in ("UPDATE", "DELETE"):
            self.db.execute(
                f"CREATE TRIGGER IF NOT EXISTS prohibit_{action.lower()} BEFORE {action} ON observations BEGIN SELECT RAISE(ABORT, 'append-only evidence'); END"
            )
        self.db.commit()

    def _object(self, content: bytes) -> str:
        if self.read_only:
            raise ValueError("只读证据档案不能写入")
        digest = sha256(content)
        path = self.root / "objects" / digest
        if path.exists():
            if sha256(path.read_bytes()) != digest:
                raise ValueError("已有证据对象被篡改")
        else:
            try:
                with path.open("xb") as file:
                    file.write(content)
            except FileExistsError:
                if sha256(path.read_bytes()) != digest:
                    raise ValueError("证据对象校验失败")
        return digest

    def _insert(self, kind, dataset, source, observed, digest, meta):
        ident = uuid4().hex
        meta = dict(
            meta,
            kind=kind,
            dataset=dataset,
            source=source,
            observed_at=observed,
            content_sha256=digest,
        )
        encoded = canonical(meta)
        self.db.execute(
            "INSERT INTO observations VALUES(?,?,?,?,?,?,?,?)",
            (ident, kind, dataset, source, observed, digest, encoded.decode(), sha256(encoded)),
        )
        self.db.commit()
        return ident

    def _read(self, ident):
        row = self.db.execute(
            "SELECT kind,dataset,source,observed,digest,metadata,metadata_hash FROM observations WHERE id=?",
            (ident,),
        ).fetchone()
        if row is None:
            raise ValueError("证据 ID 不存在")
        kind, dataset, source, observed, digest, encoded, meta_hash = row
        if sha256(encoded.encode()) != meta_hash:
            raise ValueError("证据元数据被篡改")
        meta = json.loads(encoded)
        if any(
            meta[k] != v
            for k, v in {
                "kind": kind,
                "dataset": dataset,
                "source": source,
                "observed_at": observed,
                "content_sha256": digest,
            }.items()
        ):
            raise ValueError("证据索引与元数据不一致")
        path = self.root / "objects" / digest
        content = path.read_bytes()
        if sha256(content) != digest:
            raise ValueError("证据内容被篡改")
        return content, meta

    def record_response(
        self,
        content: bytes,
        *,
        url: str,
        final_url: str,
        status: int,
        headers: dict,
        params: dict | None,
        started_at: str,
        received_at: str,
        parser_version="http-v1",
    ) -> str:
        params = params or {}
        public_metadata(url, params, headers)
        public_metadata(final_url, {}, {})
        started, received = timestamp(started_at), timestamp(received_at)
        if received < started:
            raise ValueError("响应接收时间早于请求时间")
        meta = {
            "url": url,
            "final_url": final_url,
            "status": int(status),
            "headers": headers,
            "params": params,
            "started_at": started,
            "received_at": received,
            "parser_version": parser_version,
            "byte_semantics": "requests-decoded entity body; not wire bytes",
            "authenticity": "not_authenticated_market_data",
        }
        return self._insert("response", "", "", received, self._object(content), meta)

    def record_frame(
        self,
        dataset: str,
        source: str,
        frame: pd.DataFrame,
        *,
        raw_ids: list[str],
        semantics: dict,
        observed_at: str | None = None,
    ) -> str:
        if not raw_ids or not dataset or not source:
            raise ValueError("接受的数据版本必须有响应血缘和数据集/来源")
        received = []
        for raw_id in raw_ids:
            _, meta = self._read(raw_id)
            if meta["kind"] != "response" or not 200 <= meta["status"] < 300:
                raise ValueError("数据血缘必须是成功响应")
            received.append(meta["received_at"])
        observed = timestamp(observed_at or now())
        if observed < max(received):
            raise ValueError("数据首次可得时间不能早于响应接收时间")
        if frame.empty:
            raise ValueError("不能留存空的已接受数据版本")
        normalized = frame.copy()
        if not {"date", "close"}.issubset(normalized):
            raise ValueError("数据版本缺少 date/close")
        market_dates = pd.to_datetime(normalized["date"], errors="raise")
        if market_dates.dt.tz is not None:
            from .sessions import BEIJING

            market_dates = market_dates.dt.tz_convert(BEIJING).dt.tz_localize(None)
        if market_dates.isna().any():
            raise ValueError("证据行情日期缺失")
        normalized["date"] = market_dates.dt.strftime("%Y-%m-%d")
        from .sessions import BEIJING

        observed_market_date = (
            datetime.fromisoformat(observed).astimezone(BEIJING).date().isoformat()
        )
        if (normalized["date"] > observed_market_date).any():
            raise ValueError("行情日期不能晚于首次可得日期")
        if normalized["date"].duplicated().any() or not normalized["date"].is_monotonic_increasing:
            raise ValueError("证据日期必须唯一升序")
        # to_json maps missing optional OHLC to null; nonfinite closes are never accepted.
        close = pd.to_numeric(normalized["close"], errors="raise")
        if (
            close.isna().any()
            or (close <= 0).any()
            or close.isin([float("inf"), -float("inf")]).any()
        ):
            raise ValueError("证据收盘价无效")
        payload = json.loads(
            normalized.to_json(orient="records", date_format="iso", double_precision=15)
        )
        content = canonical(payload)
        return self._insert(
            "frame",
            dataset,
            source,
            observed,
            self._object(content),
            {
                "raw_ids": list(raw_ids),
                "semantics": semantics,
                "parser_version": semantics.get("parser_version", "unknown"),
                "market_start": normalized["date"].iloc[0] if len(normalized) else None,
                "market_end": normalized["date"].iloc[-1] if len(normalized) else None,
            },
        )

    def replay_frame(self, dataset: str, knowledge_as_of: str, source: str | None = None):
        cutoff = timestamp(knowledge_as_of)
        query = "SELECT id FROM observations WHERE kind='frame' AND dataset=? AND observed<=?"
        params = [dataset, cutoff]
        if source is not None:
            query += " AND source=?"
            params.append(source)
        query += " ORDER BY observed DESC, rowid DESC LIMIT 1"
        row = self.db.execute(query, params).fetchone()
        if row is None:
            return None, {
                "status": "insufficient_evidence",
                "reason": "此前没有可证明已取得的数据版本",
            }
        content, meta = self._read(row[0])
        for raw_id in meta["raw_ids"]:
            _, raw = self._read(raw_id)
            if raw["received_at"] > cutoff or raw["received_at"] > meta["observed_at"]:
                raise ValueError("历史可得时间血缘冲突")
        frame = pd.DataFrame(json.loads(content))
        frame["date"] = pd.to_datetime(frame["date"])
        return frame, dict(meta, vintage_id=row[0], status="replayed", knowledge_as_of=cutoff)

    def verify_all(self) -> dict:
        ids = [r[0] for r in self.db.execute("SELECT id FROM observations")]
        for ident in ids:
            _, meta = self._read(ident)
            if meta["kind"] == "frame":
                for raw_id in meta["raw_ids"]:
                    _, raw = self._read(raw_id)
                    if raw["kind"] != "response" or raw["received_at"] > meta["observed_at"]:
                        raise ValueError("证据版本血缘时间冲突")
        return {"status": "passed", "observations": len(ids), "scope": "local_integrity_only"}

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
