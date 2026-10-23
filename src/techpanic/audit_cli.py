"""本地审计命令；不联网，不自动生成或上传市场真值。"""

import argparse
import json
from pathlib import Path
import pandas as pd
from .config import IndexConfig
from .errors import ConfigError


def parser():
    p = argparse.ArgumentParser(prog="techpanic audit")
    sub = p.add_subparsers(dest="command", required=True)
    ref = sub.add_parser("reference")
    ref.add_argument("--data", type=Path, required=True)
    ref.add_argument("--metadata", type=Path, required=True)
    ref.add_argument("--reference", type=Path, required=True)
    ref.add_argument("--reference-metadata", type=Path, required=True)
    impact = sub.add_parser("source-impact")
    impact.add_argument("--left", type=Path, required=True)
    impact.add_argument("--right", type=Path, required=True)
    impact.add_argument("--qvix", type=Path)
    template = sub.add_parser("protocol-template")
    freeze = sub.add_parser("freeze-protocol")
    freeze.add_argument("--protocol", type=Path, required=True)
    study = sub.add_parser("study")
    study.add_argument("--data", type=Path, required=True)
    study.add_argument("--protocol", type=Path, required=True)
    evidence = sub.add_parser("verify-evidence")
    evidence.add_argument("--directory", type=Path, required=True)
    for child in [ref, impact, template, freeze, study, evidence]:
        child.add_argument("--output", type=Path)
    return p


def main(argv):
    try:
        args = parser().parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 5
    try:
        if args.command == "reference":
            from .reference import validate_reference

            result = validate_reference(
                pd.read_csv(args.data),
                json.loads(args.metadata.read_text(encoding="utf-8-sig")),
                args.reference,
                args.reference_metadata,
            )
        elif args.command == "source-impact":
            from .source_impact import compare_sources

            result = compare_sources(
                pd.read_csv(args.left),
                pd.read_csv(args.right),
                IndexConfig(),
                qvix=pd.read_csv(args.qvix) if args.qvix else None,
            )
        elif args.command == "protocol-template":
            from .study import protocol_template

            result = protocol_template()
        elif args.command == "freeze-protocol":
            from .study import freeze_protocol

            result = freeze_protocol(json.loads(args.protocol.read_text(encoding="utf-8-sig")))
        elif args.command == "study":
            from .study import evaluate_files

            result = evaluate_files(args.data, args.protocol)
        else:
            from .evidence import EvidenceStore

            if not (args.directory / "observations.sqlite3").is_file():
                raise ValueError("没有证据档案")
            with EvidenceStore(args.directory, read_only=True) as store:
                result = store.verify_all()
        text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
        print(text, flush=True)
        return 2 if result.get("status") in {"failed", "insufficient_evidence"} else 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise ConfigError(f"审计输入无效：{exc}") from exc
