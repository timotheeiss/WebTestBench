import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Dict, Optional, Set, Type

from agent import APIConfig, BaseAgent, AGENT_REGISTRY, scrub_routing_env
from utils import *


AgentCls = Type[BaseAgent]

# Sibling replay tool used to auto-dump the agent's-eye view after each run.
DUMP_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "dump_agent_view.py"


def _dump_agent_view(output_dir: Path) -> None:
    """Best-effort: write agent_view.txt (the observations the agent received +
    the actions it took) next to this run's results, by replaying its SDK
    transcript. Runs automatically after every app; never fails the run.

    Opt out with AGENT_VIEW_DUMP=0; use AGENT_VIEW_FULL=1 for untruncated snapshots.
    """
    if os.environ.get("AGENT_VIEW_DUMP", "1") == "0":
        return
    try:
        meta = json.loads((output_dir / "session_meta.json").read_text(encoding="utf-8"))
        # The perception/action stage carries the transcript we want to replay.
        session_id = (meta.get("defect_detection") or {}).get("session_id")
        if not session_id:
            print(f"[warn] agent_view dump skipped for {output_dir}: no session_id")
            return
        cmd = [sys.executable, str(DUMP_SCRIPT), session_id,
               "--out", str(output_dir / "agent_view.txt")]
        if os.environ.get("AGENT_VIEW_FULL", "0") == "1":
            cmd.append("--full")
        subprocess.run(cmd, check=False)
    except Exception as e:
        print(f"[warn] agent_view dump skipped for {output_dir}: {e}")


def _parse_filter_ids(raw: Optional[str]) -> Optional[Set[str]]:
    if not raw:
        return None
    ids = {item.strip() for item in raw.split(",") if item.strip()}
    return ids or None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unified entrypoint to run different WebProber agents."
    )
    parser.add_argument(
        "--agent", required=True, type=str,
        help=(
            "Agent key (built-in: "
            + ", ".join(sorted(AGENT_REGISTRY.keys()))
            + ") or module:Class path (e.g. claude_code, openhands)."
        ),
    )
    parser.add_argument("--data_jsonl_path", required=True, type=str,
                        help="Path to the dataset JSONL file (each line is a record).")
    parser.add_argument("--output_root", required=True, type=str,
                        help="Root directory for all generated outputs.")
    parser.add_argument("--log_root", required=True, type=str,
                        help="Root directory for all log files produced during execution.")
    parser.add_argument("--project_root", type=str, default=None,
                        help="Root directory containing local projects (used when --use_web_url is not set).")
    parser.add_argument("--version", required=True, type=str,
                        help="Version label used to group outputs/logs (e.g. the condition).")
    parser.add_argument("--rep", type=str, default="rep1",
                        help="Repetition label; results are written per app under <app>/<rep>/.")
    parser.add_argument("--base_port", type=int, default=6000,
                        help="Base port offset for local servers (port = base_port + int(record_id[-4:])).")

    parser.add_argument("--auth_mode", type=str, default="api",
                        choices=["api", "subscription"],
                        help="'api' routes the agent at --api_base_url with --api_key; "
                             "'subscription' uses the Claude Code login on this machine.")
    parser.add_argument("--api_base_url", type=str, default=None,
                        help="Base URL for API server. Required for --auth_mode api.")
    parser.add_argument("--api_key", type=str, default=None,
                        help="API key for API server. Required for --auth_mode api.")
    parser.add_argument("--model", required=True, type=str,
                        help="Model name, e.g., claude-sonnet-4-5.")

    return parser.parse_args()


async def _run_record(
    agent_cls: AgentCls,
    record: Dict[str, str],
    api_config: APIConfig,
    args: argparse.Namespace,
    output_root: Path,
    log_root: Path,
) -> None:
    record_id = record.get("index", "")
    instruction = record.get("instruction", "")

    if not record_id:
        raise ValueError(f"Invalid record without 'index': {record}")
    if not instruction:
        raise ValueError(f"Record {record_id} missing 'instruction'.")
    
    local_project_dir = Path(args.project_root) / record_id
    server_url = f"http://localhost:{args.base_port + int(record_id[-4:])}/"
    # server_url = record.get("webpage_url", f"http://localhost:{args.base_port + int(record_id[-4:])}/")

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    # Reps are per app: <output_root>/<app>/<rep>/. Logs co-locate with results.
    output_dir = output_root / record_id / args.rep
    log_dir = log_root / record_id / args.rep
    log_file = log_dir / f"{timestamp}-eval.log"

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    log_f = None
    tee_out = None
    tee_err = None

    try:
        probe_agent = agent_cls(
            instruction=instruction,
            api_config=api_config,
            server_url=server_url,
            local_project_dir=local_project_dir,
            output_dir=output_dir,
            event_log_stream=None,
        )
        if probe_agent.result_path.exists():
            return

        log_dir.mkdir(parents=True, exist_ok=True)
        log_f = open(log_file, "w", encoding="utf-8")
        tee_out = Tee(original_stdout, log_f)
        tee_err = Tee(original_stderr, log_f)
        sys.stdout = tee_out
        sys.stderr = tee_err

        running_info = (
            f"Agent: {agent_cls.__name__}\n"
            f"Index: {record_id}\n"
            f"Instruction: {instruction}\n"
            f"Server URL: {server_url}\n"
            f"Output Dir: {output_dir}\n"
            f"Log Dir: {log_dir}"
        )
        print_boxed(running_info)

        agent = agent_cls(
            instruction=instruction,
            api_config=api_config,
            server_url=server_url,
            local_project_dir=local_project_dir,
            output_dir=output_dir,
            event_log_stream=log_f,
            record=record,
        )
        await agent.run()

        # Capture what the agent saw/did while the SDK transcript is still fresh.
        _dump_agent_view(output_dir)

    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        if tee_out or tee_err:
            for handler in logging.getLogger().handlers:
                if getattr(handler, "stream", None) is tee_out:
                    handler.stream = original_stdout
                elif getattr(handler, "stream", None) is tee_err:
                    handler.stream = original_stderr
        if log_f:
            try:
                log_f.close()
            except Exception:
                pass


async def main() -> None:
    args = parse_args()

    agent_name = args.agent
    if agent_name in AGENT_REGISTRY:
        agent_cls = AGENT_REGISTRY[agent_name]
    else:
        raise KeyError(f"Unknown agent '{agent_name}'. Available: {', '.join(sorted(AGENT_REGISTRY.keys()))}")

    if args.auth_mode == "subscription":
        scrub_routing_env()

    api_config = APIConfig(
        model=args.model,
        auth_mode=args.auth_mode,
        base_url=args.api_base_url,
        api_key=args.api_key,
    )

    data_jsonl_path = Path(args.data_jsonl_path)
    if not data_jsonl_path.exists():
        raise FileNotFoundError(f"Dataset not found: {data_jsonl_path}")

    output_root = Path(args.output_root) / args.version
    output_root.mkdir(parents=True, exist_ok=True)
    log_root = Path(args.log_root) / args.version
    log_root.mkdir(parents=True, exist_ok=True)

    with open(data_jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            record_id = record.get("index") or record.get("id")

            try:
                await _run_record(
                    agent_cls=agent_cls,
                    record=record,
                    api_config=api_config,
                    args=args,
                    output_root=output_root,
                    log_root=log_root,
                )
            except Exception:
                traceback.print_exc()
                sys.exit(1)

    print_green("🎉 All tasks finished.")


if __name__ == "__main__":
    asyncio.run(main())
