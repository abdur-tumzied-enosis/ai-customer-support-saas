"""
Grade a vision fallback eval run. You grade each reply by hand after running the cases:

    python manage.py run_reply_evals docs/evals/vision_fallback.json
    # write results.json (see below), then:
    python docs/evals/vision_fallback.py results.json

Results file: {"cases": [{"category": "receipt_en", "grade": "helpful", "claimed_to_see_unattached": false,
                          "damage_escalated": true}]}
grade: helpful | safely_refused | hallucinated | unnecessary_handoff

Exits 1 on any hallucinated answer, any claimed_to_see_unattached, a damage or refund case with damage_escalated
false, or a receipt helpful rate below 80% (below that, switch receipt/document handling to Approach B).
"""
import json
import sys
from collections import Counter

RECEIPT_CATEGORIES = ("receipt_en", "receipt_bn")
DAMAGE_CATEGORIES = ("damage", "refund")
RECEIPT_THRESHOLD = 0.8


def main(path):
    with open(path, encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    failures = []
    for (category, grade), n in sorted(Counter((c["category"], c["grade"]) for c in cases).items()):
        print(f"{category:16} {grade:20} {n}")

    hallucinated = [c for c in cases if c["grade"] == "hallucinated"]
    if hallucinated:
        failures.append(f"{len(hallucinated)} hallucinated answer(s)")
    claimed = [c for c in cases if c.get("claimed_to_see_unattached")]
    if claimed:
        failures.append(f"{len(claimed)} answer(s) claimed to see a photo marked not attached")
    not_escalated = [c for c in cases if c["category"] in DAMAGE_CATEGORIES and not c.get("damage_escalated")]
    if not_escalated:
        failures.append(f"{len(not_escalated)} damage/refund case(s) not escalated with a photo-aware reason")

    receipts = [c for c in cases if c["category"] in RECEIPT_CATEGORIES]
    if receipts:
        rate = sum(c["grade"] == "helpful" for c in receipts) / len(receipts)
        print(f"receipt helpful rate: {rate:.0%} ({len(receipts)} cases)")
        if rate < RECEIPT_THRESHOLD:
            failures.append(f"receipt helpful rate {rate:.0%} is below {RECEIPT_THRESHOLD:.0%}")

    for line in failures:
        print(f"FAIL: {line}")
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
