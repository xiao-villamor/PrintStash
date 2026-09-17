"""Reproduce short-query floors using calibration labels only.

Run ``python -m tests.fakes.search_clarity_calibrate`` from backend. The policy
is maximum unrelated cosine plus 0.01, rounded upward to 0.01. Report native
positive recall separately from functional lexical recovery.
"""

import base64
import hashlib
import json
import math

import numpy as np
from printstash_core.search.passages import PassageContent, render_passages

from tests.paths import FIXTURES_DIR


def calibrate():
    root = FIXTURES_DIR / "search"
    corpus = json.loads((root / "clarity-corpus.json").read_text())
    measured = json.loads((root / "clarity-vectors.json").read_text())
    rows = corpus["calibration"]
    result = {}
    for kind in ("text", "clip"):
        data = measured[kind]

        def decode(blob):
            return np.frombuffer(base64.b64decode(blob), dtype="<f4")

        documents = np.array(
            [
                decode(
                    data["vectors"][
                        hashlib.sha256(
                            render_passages(
                                PassageContent(
                                    title=row["name"], description=row["description"]
                                )
                            )[0].text.encode()
                        ).hexdigest()
                    ]
                )
                if kind == "text"
                else decode(data["images"][row["id"]][0]["vector"])
                for row in rows
            ]
        )
        negatives, positives = [], []
        for query in [row["query"] for row in rows] + corpus["calibration_absent"]:
            vector = decode(
                data["vectors"][
                    hashlib.sha256(
                        (data["space"]["query_prefix"] + query).encode()
                    ).hexdigest()
                ]
            )
            for row, score in zip(rows, documents @ vector, strict=True):
                (positives if row["query"] == query else negatives).append(float(score))
        floor = math.ceil((max(negatives) + 0.01) * 100) / 100
        result[kind] = {
            "maximum_negative": max(negatives),
            "floor": floor,
            "native_positive_recall": sum(value >= floor for value in positives)
            / len(positives),
        }
    return result


if __name__ == "__main__":
    print(json.dumps(calibrate(), indent=2))
