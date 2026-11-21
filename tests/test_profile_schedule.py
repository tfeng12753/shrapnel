import unittest

from tools.profile_schedule import schedule_profile


class ScheduleProfileTests(unittest.TestCase):
    def test_worker_startups_and_missed_commit_penalty(self):
        tests = [{"number": i, "id": str(i), "seconds": s}
                 for i, s in enumerate([8, 3, 1])]
        profile = schedule_profile(
            b'{"commit":"x","workers":[[0],[],[1]]}\n{"commit":"y","workers":[]}\n',
            tests, {"x": {"failing": []}, "y": {"failing": [1, 2]}})
        first, missed = profile["patches"]
        self.assertEqual(profile["simulated_ms"], 312000)
        self.assertEqual(first["excess_schedule_ms"], 12000)
        self.assertEqual([t["single_removal_saving_ms"] for t in first["top_extra_tests"]],
                         [7000, 2000])
        self.assertEqual(missed["miss_penalty_ms"], 300000)
        self.assertEqual(missed["missing"], [1, 2])
        self.assertEqual(missed["excess_schedule_ms"], -6000)
