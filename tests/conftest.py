"""Keep the offline suite on the shipped Bedrock file.

A machine may set `config/bedrock.local.yaml` with `enabled: true`. Tests pin
`NBA_BEDROCK_FILE` so pytest does not call AWS.
"""

import os
from pathlib import Path

os.environ["NBA_BEDROCK_FILE"] = str(
    Path(__file__).resolve().parents[1] / "config" / "bedrock.example.yaml"
)
