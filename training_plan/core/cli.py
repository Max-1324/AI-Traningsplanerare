import argparse
import os


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", "-p", choices=["openai", "anthropic", "gemini", "ollama"], default=os.getenv("AI_PROVIDER", "gemini"))
    parser.add_argument("--provider-gen", dest="provider_gen", choices=["openai", "anthropic", "gemini", "ollama"], default=os.getenv("AI_PROVIDER_gen_revision"), help="Provider for plan generation and revision (overrides --provider)")
    parser.add_argument("--provider-review", dest="provider_review", choices=["openai", "anthropic", "gemini", "ollama"], default=os.getenv("AI_PROVIDER_review"), help="Provider for plan review (overrides --provider)")
    parser.add_argument("--days-history", type=int, default=60)
    parser.add_argument("--horizon", type=int, default=14)
    parser.add_argument("--auto", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def parse_args(argv=None):
    return build_parser().parse_args(argv)
