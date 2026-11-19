# -*- coding: utf-8 -*-
"""Safe, synthetic offline tests: no media/app/native imports or disk writes."""

import copy
import pathlib
import unittest
from unittest import mock

from preferences import filter_library
from video_categories import (
    CATEGORY_LABELS, SCENERY_CATEGORIES, category_counts, classify_video,
    filter_scenery,
)


class CategoryContractTests(unittest.TestCase):
    def test_category_order_and_labels(self):
        expected = (
            ("all", "全部"), ("ocean", "海洋"), ("mountains", "山川"),
            ("cities", "城市"), ("desert", "沙漠"), ("space", "太空"),
            ("ice", "冰雪"), ("forest", "森林"), ("other", "其他"),
        )
        self.assertIsInstance(SCENERY_CATEGORIES, tuple)
        self.assertEqual(SCENERY_CATEGORIES, expected)
        self.assertEqual(CATEGORY_LABELS, dict(expected))


class ClassifyVideoTests(unittest.TestCase):
    def assertCategory(self, expected, *names):
        for name in names:
            with self.subTest(name=name):
                actual = classify_video(name)
                self.assertEqual(actual, expected)
                self.assertIn(actual, CATEGORY_LABELS)
                self.assertNotEqual(actual, "all")

    def test_all_known_orbital_stock_routes_and_resolution_suffixes(self):
        stems = (
            "Africa_Night_1", "Africa_MiddleEast_2", "Antarctica_5",
            "Atlantic_SpainFrance_6", "Australia_7", "California_to_Vegas_12",
            "Caribbean_13", "Caribbean_Day_15", "Iran_Afghanistan_44",
            "Ireland_to_Asia_45", "Italy_to_Asia_46", "Korea_Japan_Night_49",
            "New_York_Night_66", "New_Zealand_67", "Nile_Delta_68",
            "North_America_Aurora_69", "Sahara_Italy_75",
            "SouthAfrica_NorthAsia_85", "SoCal_Baja_86", "WestAfrica_Alps_89",
        )
        for stem in stems:
            for suffix in ("", "_4K", "_1080", "_1080p"):
                self.assertCategory("space", stem + suffix + ".mov")

    def test_stock_identity_is_case_and_separator_insensitive(self):
        self.assertCategory("space", "AUSTRALIA-7-4k.MOV",
                            "new zealand 67 1080P.mp4", "Sahara_Italy75_4K.mov",
                            "NEW.YORK.NIGHT.66.4K.MOV", "antarctica5.mp4")

    def test_verified_great_wall_china_range(self):
        for number in range(16, 23):
            self.assertCategory("mountains", "China_{}_4K.mov".format(number),
                                "China_{}_1080p.mov".format(number))
        self.assertCategory("other", "China_15_4K.mov", "China_23_4K.mov")

    def test_verified_hawaii_coastal_range(self):
        for number in range(34, 39):
            self.assertCategory("ocean", "Hawaii_{}_4K.mov".format(number))
        self.assertCategory("other", "Hawaii_33_4K.mov", "Hawaii_39_4K.mov")

    def test_greenland_ice_and_liwa_desert(self):
        for number in range(31, 34):
            self.assertCategory("ice", "Greenland_{}_4K.mov".format(number))
        self.assertCategory("desert", "Liwa_51_4K.mov", "Liwa_51_1080p.mov")

    def test_antarctica_number_five_override_not_generic_country(self):
        self.assertCategory("space", "Antarctica_5_4K.mov", "Antarctica_5.mov")
        self.assertCategory("ice", "Antarctica.mov", "Antarctica_landscape.mov",
                            "Antarctica_6_4K.mov")
        self.assertCategory("ocean", "Antarctica_coast.mov")

    def test_stock_number_and_complete_identity_required(self):
        self.assertCategory("ocean", "Australia_beach.mp4")
        self.assertCategory("cities", "New_York_Night.mp4", "New_York_Night_65_4K.mov")
        self.assertCategory("other", "Australia_8_4K.mov", "Australia_70_4K.mov",
                            "Australia_7_4K_backup.mov", "Australia_7_720p.mov",
                            "New_Zealand_67_extra.mov", "my_Australia_7.mov",
                            "我的Australia_7_4K.mov")
        self.assertCategory("desert", "Sahara.mp4", "Sahara_Italy_76_4K.mov")
        self.assertCategory("mountains", "WestAfrica_Alps_90_4K.mov")

    def test_actual_marine_stock_names(self):
        self.assertCategory(
            "ocean", "Alaskan_Jellies_3_4K.mov", "CA_Kelp_Forest_11_4K.mov",
            "Cownose_Rays_24_4K.mov", "Humpback_Whale_43_4K.mov",
            "Palau_Coral_70_4K.mov", "Palau_Jellies_73_4K.mov",
            "Red_Sea_Coral_74_4K.mov", "Tahiti_Waves_87_4K.mov",
            "Gray_Reef_Sharks.mov", "California_Dolphins.mov", "Sea_Stars.mov",
            "Costa_Rica_Dolphins.mov", "Seals.mov", "Barracuda.mov",
            "Jacks.mov", "Bumpheads.mov",
        )

    def test_actual_city_filenames(self):
        self.assertCategory(
            "cities", "HongKong_4K.mov", "Golden_Gate_Day_Sonoma_4K.mov",
            "Golden_Gate_Night_Sonoma_4K.mov", "Golden_Gate_Evening_Sonoma_4K.mov",
            "Golden_Gate_Sunset_Sonoma_4K.mov", "Golden_Gate_A006_4K.mov",
            "Golden_Gate_A008_4K.mov", "Golden_Gate_A013_4K.mov",
            "Golden_Gate_A076_4K.mov",
        )

    def test_generic_english_categories(self):
        samples = {
            "ocean": ("blue_ocean", "quiet-sea", "jellyfish", "underwater_reef"),
            "mountains": ("alpine_valley", "mountain_peaks", "Great_Wall"),
            "cities": ("Hong-Kong-night", "New_York_Night", "city_skyline"),
            "desert": ("sand_dunes", "Sahara", "Gobi_desert"),
            "space": ("earth_from_orbit", "Milky_Way", "north_aurora"),
            "ice": ("glacier", "snowy_peaks", "icebergs"),
            "forest": ("rainforest", "bamboo_woods", "green_forest"),
            "other": ("untitled", "holiday_001", "Australia", "New_Zealand"),
        }
        for expected, stems in samples.items():
            self.assertCategory(expected, *(stem + ".mp4" for stem in stems))

    def test_preferences_panel_fixture_names(self):
        self.assertCategory("mountains", "Alpine_Dawn_4K.mov")
        self.assertCategory("ocean", "Coastline_04_4K.mov", "Palau_Coral_70_4K.mov",
                            "Reef_Overhead.webm")
        self.assertCategory("forest", "Forest_Morning.mp4")
        self.assertCategory("cities", "夜色_城市.mkv")

    def test_generic_chinese_categories(self):
        samples = {
            "ocean": ("宁静海洋", "帕劳水母", "红海珊瑚", "牛鼻鲼群", "巨藻林"),
            "mountains": ("中国山水", "万里长城", "阿尔卑斯山脉", "清晨峡谷"),
            "cities": ("香港夜景", "纽约天际线", "金门大桥", "城市灯火"),
            "desert": ("利瓦沙漠", "金色沙丘", "戈壁日落"),
            "space": ("地球轨道", "北美极光", "银河星空"),
            "ice": ("格陵兰冰川", "雪山日出", "南极冰原"),
            "forest": ("热带雨林", "绿色森林", "山间竹林"),
            "other": ("我的假期", "未命名影片"),
        }
        for expected, stems in samples.items():
            self.assertCategory(expected, *(stem + ".mov" for stem in stems))

    def test_case_separators_and_phrase_adjacency(self):
        self.assertCategory("ocean", "ReD_sEa-CoRaL.MOV", "COWNOSE.RAYS.mp4")
        self.assertCategory("cities", "NEW__YORK---NIGHT.MP4", "Hong Kong.mov")
        self.assertCategory("other", "new_something_york.mov", "cownose_other_rays.mov")

    def test_whole_english_tokens_prevent_substring_collisions(self):
        self.assertCategory("other", "coraline.mov", "season.mp4", "seaside.mp4",
                            "earthworm.mov", "spacecrafting.mov", "forestry.mp4",
                            "deserter.mov", "cityscapeish.mov", "mountaineer.mov",
                            "jellyfishlike.mp4", "unicorn.mov")

    def test_collision_precedence(self):
        self.assertCategory("ocean", "kelp_forest.mov", "CA_Kelp_Forest_11_4K.mov",
                            "海藻森林.mov", "coral_mountain.mov")
        self.assertCategory("space", "Sahara_Italy_75_4K.mov", "earth_ocean.mov")
        self.assertCategory("desert", "Sahara_Italy.mov")
        self.assertCategory("ice", "snowy_mountain.mov", "冰川山脉.mov")

    def test_only_basename_not_folders_or_extension(self):
        self.assertCategory("other", r"X:\ocean\space\unknown.mp4",
                            "/forest/desert/unknown.mov", "plain.coral")
        self.assertCategory("forest", r"X:\space\ocean\forest.mov")
        self.assertCategory("cities", "/ice/sea/New_York_Night.mov")
        self.assertCategory("other", "X:\\ocean\\", "/space/")

    def test_pathlike_and_invalid_paths(self):
        self.assertCategory("ocean", pathlib.PureWindowsPath(r"X:\forest\coral.mov"),
                            b"X:\\space\\ocean.mov")
        self.assertCategory("other", "", None, 42, [], {})

    def test_repeated_calls_are_stable(self):
        names = ("Sahara_Italy75_4K.mov", "kelp_forest.mov", "coraline.mov")
        expected = [classify_video(name) for name in names]
        self.assertEqual([classify_video(name) for name in reversed(names)],
                         list(reversed(expected)))


class CollectionTests(unittest.TestCase):
    def setUp(self):
        # Duplicated paths and display names intentionally preserve distinct indexes.
        self.items = [
            ("Ocean favorite", r"X:\space\Palau_Coral_70_4K.mov", "forest.jpg"),
            ("Night favorite", r"X:\ocean\New_York_Night_66_4K.mov", "city.jpg"),
            ("Night favorite", "New_York_Night.mp4", "space.jpg"),
            ("Forest favorite", "CA_Kelp_Forest_11_4K.mov", "ice.jpg"),
            ("Great Wall", "China_18_4K.mov", "ocean.jpg"),
            ("Plain", "unknown.mov", "space_favorite.jpg"),
            ("Ocean favorite", r"X:\space\Palau_Coral_70_4K.mov", "other.jpg"),
            ("Winter", "Greenland_32_4K.mov", None),
            ("Dunes", "Liwa_51_4K.mov", "ocean.jpg"),
            ("Trees", "forest.mp4", "ocean.jpg"),
        ]

    def test_counts_all_categories_and_partition_sum(self):
        counts = category_counts(self.items)
        self.assertEqual(list(counts), [key for key, _ in SCENERY_CATEGORIES])
        self.assertEqual(counts, {"all": 10, "ocean": 3, "mountains": 1,
                                 "cities": 1, "desert": 1, "space": 1,
                                 "ice": 1, "forest": 1, "other": 1})
        self.assertEqual(sum(value for key, value in counts.items() if key != "all"),
                         counts["all"])

    def test_empty_counts_and_filters(self):
        self.assertEqual(category_counts([]), dict.fromkeys(CATEGORY_LABELS, 0))
        self.assertEqual(filter_scenery([], "", "ocean"), [])
        self.assertEqual(filter_scenery([], "missing", "all"), [])

    def test_counts_do_not_require_indexable_items(self):
        self.assertEqual(category_counts(iter(self.items)), category_counts(self.items))

    def test_combined_search_category_original_index_fidelity(self):
        self.assertEqual(filter_scenery(self.items, "favorite", "ocean"), [0, 3, 6])
        self.assertEqual(filter_scenery(self.items, "NIGHT", "space"), [1])
        self.assertEqual(filter_scenery(self.items, "night", "cities"), [2])
        self.assertEqual(filter_scenery(self.items, "favorite coral", "ocean"), [0, 6])
        self.assertEqual(filter_scenery(self.items, "great 18", "mountains"), [4])
        self.assertEqual(filter_scenery(self.items, "forest", "forest"), [9])
        self.assertEqual(filter_scenery(self.items, "missing", "ocean"), [])

    def test_default_and_invalid_category_keep_existing_search_behavior(self):
        for query in ("", "   ", "FAVORITE", "night 66", "great 18", "favorite coral",
                      "forest", "海洋", "missing"):
            expected = filter_library(self.items, query)
            self.assertEqual(filter_scenery(self.items, query), expected)
            for invalid in ("unknown", "海洋", "OCEAN", "", None, [], {}, 42):
                with self.subTest(query=query, category=invalid):
                    self.assertEqual(filter_scenery(self.items, query, invalid), expected)

    def test_every_category_filter_matches_original_partition(self):
        counts = category_counts(self.items)
        for category in CATEGORY_LABELS:
            matches = filter_scenery(self.items, category=category)
            self.assertEqual(len(matches), counts[category])
            self.assertEqual(matches, sorted(matches))

    def test_names_directories_and_thumbnails_are_not_classification_evidence(self):
        items = [("Ocean space forest", r"X:\sea\orbit\untitled.mov", "coral.jpg")]
        self.assertEqual(category_counts(items)["other"], 1)
        self.assertEqual(filter_scenery(items, "ocean", "other"), [0])
        self.assertEqual(filter_scenery(items, "", "space"), [])
        self.assertEqual(filter_scenery(items, "orbit"), [])
        self.assertEqual(filter_scenery(items, "coral"), [])

    def test_no_mutation_and_tuple_sequences_supported(self):
        before = copy.deepcopy(self.items)
        identities = [id(item) for item in self.items]
        category_counts(self.items)
        filter_scenery(self.items, "favorite", "ocean")
        self.assertEqual(self.items, before)
        self.assertEqual([id(item) for item in self.items], identities)
        self.assertEqual(filter_scenery(tuple(self.items), "night", "space"), [1])

    def test_helpers_do_not_access_filesystem(self):
        with mock.patch("builtins.open", side_effect=AssertionError("file access")), \
                mock.patch("os.stat", side_effect=AssertionError("stat access")), \
                mock.patch("os.listdir", side_effect=AssertionError("directory access")), \
                mock.patch("os.scandir", side_effect=AssertionError("directory access")):
            self.assertEqual(classify_video(r"Z:\does-not-exist\Australia_7_4K.mov"), "space")
            self.assertEqual(category_counts(self.items)["all"], 10)
            self.assertEqual(filter_scenery(self.items, "night", "space"), [1])


if __name__ == "__main__":
    unittest.main()
