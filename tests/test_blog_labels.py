"""Short synthetic examples: plans and other fish are not hammer observations."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data"))
from structure_full import detect_hammer, detect_size


class BlogLabelTests(unittest.TestCase):
    def test_absent_today_and_hoped_for_future_school_is_negative(self):
        body = "本日はハンマーとの遭遇はありませんでした。イサキの大群を観察。次回はハンマーの大群に会えますように！"
        self.assertIs(detect_hammer(body)[0], False)
        self.assertIsNone(detect_size(body))

    def test_explicit_zero_is_not_overridden_by_tomorrows_appearance(self):
        body = "ハンマーが0匹でした。明後日のハンマー登場を願います。"
        self.assertIs(detect_hammer(body)[0], False)
        self.assertIsNone(detect_size(body))

    def test_small_sighting_with_no_photo_and_future_school_is_small(self):
        body = "数匹のハンマーを確認できましたが、残念ながら写真はありません。明日は大きな群れに期待します。"
        self.assertIs(detect_hammer(body)[0], True)
        self.assertEqual(detect_size(body), "small")

    def test_single_in_hammer_paragraph_is_not_a_school_of_other_fish(self):
        body = "本日のハンマー情報！\n透明度は良好。\n潮は弱い。\n確認できたのは単体のみ。\nハンマーの生存確認ができました。\nタカベの群れを観察しました。"
        self.assertIs(detect_hammer(body)[0], True)
        self.assertEqual(detect_size(body), "single")

    def test_one_confirmed_sighting_wins_over_other_unsuccessful_dives(self):
        body = "ハンマーには会えず。次のダイブでもハンマーは不発。最後にハンマーを観察しました。"
        self.assertIs(detect_hammer(body)[0], True)

    def test_negated_school_and_future_only_mentions_are_not_positive(self):
        self.assertIs(detect_hammer("ハンマーの群れは見られず。次回のハンマー大群に期待。")[0], False)
        self.assertIsNone(detect_hammer("明日はハンマーの大群を狙います。")[0])

    def test_other_fish_counts_and_historical_sightings_do_not_supply_todays_label(self):
        body = "昨日はハンマーの大群を見ました。今日はイサキ100匹を観察しました。"
        self.assertIsNone(detect_hammer(body)[0])
        self.assertIsNone(detect_size(body))

    def test_current_direct_large_sighting_still_survives(self):
        body = "本日はハンマー100匹を観察しました。"
        self.assertIs(detect_hammer(body)[0], True)
        self.assertEqual(detect_size(body), "large")

    def test_no_school_but_single_found_and_present_tense_sighting_survive(self):
        body = "ハンマーの群れには会えませんでしたが、単体のハンマーを発見。"
        self.assertIs(detect_hammer(body)[0], True)
        self.assertEqual(detect_size(body), "single")
        self.assertIs(detect_hammer("みなさんハンマー見れてます。")[0], True)

    def test_hammer_topic_keeps_a_following_observed_count(self):
        body = "本日のハンマー情報。ハンマーは健在。100匹の大群れまで登場しました。"
        self.assertIs(detect_hammer(body)[0], True)
        self.assertEqual(detect_size(body), "large")


if __name__ == "__main__":
    unittest.main()
