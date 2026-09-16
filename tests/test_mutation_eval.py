"""Accounting tests do not require mutmut, WSL or an LLM."""
import unittest

from mutation_eval import select_cases
from mutation_worker import score_statuses


class MutationAccountingTests(unittest.TestCase):
    def test_unknown_and_timeout_are_not_kills(self):
        report = score_statuses({"a": "killed", "b": "timeout", "c": "not checked"})
        self.assertIsNone(report["mutation_score"])
        self.assertEqual(report["killed_fraction_lower_bound"], 1 / 3)

    def test_no_tests_remains_in_denominator(self):
        report = score_statuses({"a": "killed", "b": "survived", "c": "no tests"})
        self.assertEqual(report["mutation_score"], 1 / 3)

    def test_deduplicate_metadata_and_fixed_budget(self):
        cases = [{"x": 1, "__source": "llm"}, {"x": 1}, {"x": 2}]
        self.assertEqual(select_cases(cases, None, 42), [{"x": 1}, {"x": 2}])
        with self.assertRaises(ValueError):
            select_cases(cases, 3, 42)
        self.assertEqual(select_cases(cases, 1, 42), select_cases(cases, 1, 42))

    def test_empty_suite_not_a_zero_cost_success(self):
        with self.assertRaises(ValueError):
            select_cases([], None, 42)


if __name__ == "__main__":
    unittest.main()
