"""Write the OpenAPI spec to a file.

    python -m app.openapi_export ../docs/openapi.json [--version X.Y.Z]

CI checks that docs/openapi.json is current, and attaches a versioned copy to
every GitHub release.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--version", default=None, help="Override info.version.")
    args = parser.parse_args()

    from app.main import app

    spec = app.openapi()
    if args.version:
        spec["info"]["version"] = args.version
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(spec, indent=2, sort_keys=False) + "\n")
    print(f"Wrote {args.output} ({len(spec['paths'])} paths)")


if __name__ == "__main__":
    main()
