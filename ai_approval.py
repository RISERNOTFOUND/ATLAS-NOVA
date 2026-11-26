"""One-shot Y/N approval prompt used by the Windows ATLAS bot."""

import sys
from pathlib import Path


def main():
    if len(sys.argv) < 3:
        raise SystemExit("Approval request is incomplete.")
    request_id, description = sys.argv[1], sys.argv[2]
    if len(request_id) != 32 or any(char not in "0123456789abcdef" for char in request_id):
        raise SystemExit("Invalid approval request ID.")
    result_path = Path(__file__).resolve().parent / "ai_approvals" / f"{request_id}.txt"
    print("ATLAS ACTION APPROVAL")
    print("=" * 60)
    print(description)
    print("=" * 60)
    while True:
        answer = input("Approve this action? [Y/N]: ").strip().casefold()
        if answer in {"y", "yes"}:
            decision = "Y"
            break
        if answer in {"n", "no"}:
            decision = "N"
            break
        print("Please type Y or N.")
    result_path.parent.mkdir(exist_ok=True)
    temp_path = result_path.with_suffix(".tmp")
    temp_path.write_text(decision, encoding="utf-8")
    temp_path.replace(result_path)
    print("Approved." if decision == "Y" else "Cancelled. No action was taken.")
    input("Press Enter to close this window.")


if __name__ == "__main__":
    main()
