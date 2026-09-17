"""The build mandate's failure-injection cases A–F, mapped to the tests that prove them.

Phase 12 (2026-09-17). Each case already has a test under the module
that owns the mechanism; this file pins the mapping so a rename or a
deletion there fails loudly here, and so the report can cite one place.

A. route dies after item 6/20 → breaker trips, no cascade, route
   recovers, resume at item 7, same run id
B. process dies mid-fill → restart resumes the same run; an unverified
   item is presence-checked before replay; no duplicate
C. retailer cart read lies / returns zero → UNKNOWN or failed read,
   never EMPTY
D. add click succeeds visually but verification signals disagree →
   UNVERIFIED
E. explicit correction "X במקום Y" → Y rejected, X preferred, Y removed
   where supported, otherwise an honest exception
F. item already added before restart but stored unverified → presence
   check verifies it, no re-add
"""
import importlib
import unittest

CASES = {
    "A": [("tests.test_breaker", "test_route_dies_at_7_recovers_and_resumes_at_7_same_run")],
    "B": [("tests.test_startup_resume", "test_a_crashed_run_is_resumed_under_its_own_id"),
          ("tests.test_startup_resume", "test_the_unverified_item_is_not_replayed_when_presence_is_unknown")],
    "C": [("tests.test_truthful_results", "test_tivtaam_lines_empty_with_total_and_no_count_is_a_failed_read"),
          ("tests.test_truthful_results", "test_shufersal_zero_lines_with_a_total_is_a_failed_read"),
          ("tests.test_truthful_results", "test_absent_from_a_partial_read_is_unknown")],
    "D": [("tests.test_truthful_results", "test_count_delta_but_name_absent_is_unverified"),
          ("tests.test_truthful_results", "test_added_without_evidence_is_unverified_not_verified")],
    "E": [("tests.test_replace", "test_shufersal_replace_rejects_old_adds_new_removes_old"),
          ("tests.test_replace", "test_tivtaam_replace_adds_and_says_it_cannot_remove")],
    "F": [("tests.test_startup_resume", "test_a_crashed_run_is_resumed_under_its_own_id"),
          ("tests.test_resume", None)],
}


def _has_test(module_name: str, test_name: str | None) -> bool:
    module = importlib.import_module(module_name)
    if test_name is None:
        return any(
            attr.startswith("test_")
            for cls in vars(module).values() if isinstance(cls, type) and issubclass(cls, unittest.TestCase)
            for attr in vars(cls)
        )
    return any(
        hasattr(cls, test_name)
        for cls in vars(module).values() if isinstance(cls, type) and issubclass(cls, unittest.TestCase)
    )


class MappingTests(unittest.TestCase):
    def test_every_case_is_backed_by_a_real_test(self):
        for case, refs in CASES.items():
            for module_name, test_name in refs:
                self.assertTrue(_has_test(module_name, test_name), f"{case}: {module_name}.{test_name}")


if __name__ == "__main__":
    unittest.main()
