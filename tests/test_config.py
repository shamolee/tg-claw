"""config.py unit tests — auto-resume settings, project settings."""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src"))

from config import (
    AUTO_RESUME_ENABLED, AUTO_RESUME_MODE, AUTO_RESUME_PROMPTS,
    PROJECTS, HEALTH_CHECK_INTERVAL, STUCK_THRESHOLD,
)


def test_auto_resume_mode_is_resume():
    """Default mode is resume"""
    assert AUTO_RESUME_MODE == "resume"


def test_auto_resume_prompts_all_modes():
    """resume/check/none prompts are all defined"""
    assert "resume" in AUTO_RESUME_PROMPTS
    assert "check" in AUTO_RESUME_PROMPTS
    assert "none" in AUTO_RESUME_PROMPTS
    assert AUTO_RESUME_PROMPTS["resume"] is not None
    assert AUTO_RESUME_PROMPTS["check"] is not None
    assert AUTO_RESUME_PROMPTS["none"] is None


def test_auto_resume_enabled():
    assert AUTO_RESUME_ENABLED is True


def test_projects_defined():
    """At least one project defined"""
    assert len(PROJECTS) >= 1
    for name, config in PROJECTS.items():
        assert "bot_token" in config
        assert "bot_id" in config
        assert "cwd" in config


def test_health_check_interval():
    assert HEALTH_CHECK_INTERVAL > 0


def test_stuck_threshold():
    assert STUCK_THRESHOLD > 0


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  OK  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
