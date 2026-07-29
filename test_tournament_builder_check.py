#!/usr/bin/env python3
"""Validate bracket designs produced by the JS builder against the REAL rules.

Reads [{"label": …, "design": {"matches": […]}}, …] on stdin and writes
[{"label": …, "errors": [...]}, …] on stdout. Used by test_tournament_builder.js
so the client-side builder can never generate a bracket the server would reject.
"""

import json
import sys

from tournament_engine import (
    CustomBracket, TournamentConfig, validate_config, validate_custom_bracket,
    custom_bracket_summary, Bracket,
)


def main() -> None:
    jobs = json.load(sys.stdin)
    out = []
    for job in jobs:
        label = job.get("label", "?")
        errors = []
        try:
            spec = CustomBracket.from_dict(job.get("design") or {})
            errors = list(validate_custom_bracket(spec))
            if not errors:
                # A design must also survive the full create path: config validation
                # and compiling into the runtime bracket.
                cfg = TournamentConfig(
                    total_capacity=spec.entry_count(),
                    players_per_match=spec.max_capacity(),
                    custom_graph=spec.to_dict(),
                )
                errors = list(validate_config(cfg))
                if not errors:
                    br = Bracket.build_custom(cfg)
                    summary = custom_bracket_summary(spec)
                    if len(br.rounds[-1]) != 1:
                        errors.append("compiled bracket's last round is not a single Final")
                    if len(br.entry_slots()) != summary["tournament_size"]:
                        errors.append("entry slots do not match the summary size")
        except Exception as exc:  # noqa: BLE001
            errors = [f"exception: {exc}"]
        out.append({"label": label, "errors": errors})
    json.dump(out, sys.stdout)


if __name__ == "__main__":
    main()
