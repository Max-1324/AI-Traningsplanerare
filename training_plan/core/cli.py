import argparse
import os

ENGINES = ("deterministic", "legacy")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    _PROVIDERS = ["openai", "anthropic", "gemini", "ollama", "groq", "mistral"]
    parser.add_argument("--provider", "-p", choices=_PROVIDERS, default=os.getenv("AI_PROVIDER", "gemini"))
    parser.add_argument("--provider-gen", dest="provider_gen", choices=_PROVIDERS, default=os.getenv("AI_PROVIDER_gen_revision"), help="Provider for plan generation and revision (overrides --provider)")
    parser.add_argument("--provider-review", dest="provider_review", choices=_PROVIDERS, default=os.getenv("AI_PROVIDER_review"), help="Provider for plan review (overrides --provider)")
    parser.add_argument("--engine", choices=ENGINES, default=os.getenv("PLANNER_ENGINE", "deterministic").lower(),
                        help="deterministic: code plans the load, AI enriches (default). legacy: the old AI-first pipeline.")
    parser.add_argument("--no-ai", dest="no_ai", action="store_true",
                        default=os.getenv("PLANNER_AI", "on").strip().lower() in ("off", "0", "false", "no"),
                        help="Deterministic engine only: skip the AI enrichment call entirely.")
    parser.add_argument("--days-history", type=int, default=60)
    parser.add_argument("--horizon", type=int, default=None,
                        help="Days ahead to plan (default: DETAIL_HORIZON_DAYS=9 for deterministic, 28 for legacy).")
    parser.add_argument("--auto", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def parse_args(argv=None):
    args = build_parser().parse_args(argv)
    if args.engine not in ENGINES:
        args.engine = "deterministic"
    if args.horizon is None:
        args.horizon = int(os.getenv("DETAIL_HORIZON_DAYS", "9")) if args.engine == "deterministic" else 28
    return args
