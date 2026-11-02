"""AI 评审门禁本地规则测试。"""

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ai_review.py"
spec = importlib.util.spec_from_file_location("ai_review_script", SCRIPT)
ai_review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ai_review)


def test_high_risk_path_does_not_create_a_defect():
    level, reasons = ai_review.assess_path_risk(
        ["backend/alembic/versions/safe_migration.py"]
    )
    assert level == "HIGH"
    assert reasons
    assert not ai_review.has_blocking_defect(
        {"riskLevel": "LOW", "issues": []}
    )


def test_only_evidence_backed_high_issue_blocks():
    assert not ai_review.has_blocking_defect(
        {
            "riskLevel": "HIGH",
            "issues": [],
        }
    )
    assert not ai_review.has_blocking_defect(
        {
            "riskLevel": "HIGH",
            "issues": [
                {
                    "severity": "minor",
                    "file": "backend/app/core/config.py",
                    "problem": "style",
                }
            ],
        }
    )
    assert ai_review.has_blocking_defect(
        {
            "riskLevel": "HIGH",
            "issues": [
                {
                    "severity": "major",
                    "file": "backend/app/core/config.py",
                    "problem": "production accepts a placeholder secret",
                }
            ],
        }
    )
