# -*- coding: utf-8 -*-
"""Offline scenery classification for preferences-library tuples.

Only the video basename is classified; display names, parent directories and
thumbnail names are deliberately not scenery evidence. No media, filesystem,
network, configuration or native-application access is performed.
"""

import ntpath
import os
import re

from preferences import filter_library


SCENERY_CATEGORIES = (
    ("all", "全部"),
    ("ocean", "海洋"),
    ("mountains", "山川"),
    ("cities", "城市"),
    ("desert", "沙漠"),
    ("space", "太空"),
    ("ice", "冰雪"),
    ("forest", "森林"),
    ("other", "其他"),
)
CATEGORY_LABELS = dict(SCENERY_CATEGORIES)


def _english_tokens(text):
    # Split underscores, punctuation and letter/number boundaries, not substrings
    # within words: "sea" must not match "season", nor "coral" "coraline".
    return tuple(re.findall(r"[a-z]+|[0-9]+", text))


# Exact stock label + catalog-number identities, NOT broad country-name rules.
# The catalog's friendly nature labels are misleading for these orbital shots.
# In particular Antarctica #5 shows Earth's limb/aurora, not ground-level ice.
_STOCK_SPACE = (
    "Africa_Night_1", "Africa_MiddleEast_2", "Antarctica_5",
    "Atlantic_SpainFrance_6", "Australia_7", "California_to_Vegas_12",
    "Caribbean_13", "Caribbean_Day_15", "Iran_Afghanistan_44",
    "Ireland_to_Asia_45", "Italy_to_Asia_46", "Korea_Japan_Night_49",
    "New_York_Night_66", "New_Zealand_67", "Nile_Delta_68",
    "North_America_Aurora_69", "Sahara_Italy_75",
    "SouthAfrica_NorthAsia_85", "SoCal_Baja_86", "WestAfrica_Alps_89",
)
_STOCK_CATEGORIES = {
    _english_tokens(stem.casefold()): "space" for stem in _STOCK_SPACE
}
_STOCK_CATEGORIES.update({("china", str(n)): "mountains" for n in range(16, 23)})
_STOCK_CATEGORIES.update({("hawaii", str(n)): "ocean" for n in range(34, 39)})
_STOCK_CATEGORIES.update({("greenland", str(n)): "ice" for n in range(31, 34)})
_STOCK_CATEGORIES[("liwa", "51")] = "desert"

# After exact catalog matches, use this deterministic collision precedence.
# Marine terms precede forest (kelp forest); ice precedes mountains (snowy peaks).
# English entries match whole tokens/phrases; Chinese entries match substrings
# because Chinese filenames generally do not have word separators.
_KEYWORDS = (
    ("space",
     ("space", "orbit", "orbital", "earth", "planet", "galaxy", "nebula",
      "astronaut", "satellite", "iss", "aurora", "milky way"),
     ("太空", "宇宙", "星空", "星云", "银河", "地球", "轨道", "极光")),
    ("ocean",
     ("ocean", "sea", "marine", "underwater", "beach", "beaches", "coast",
      "coastal", "coastline", "coral", "reef", "reefs", "kelp", "jellyfish",
      "jellies", "whale", "whales", "dolphin", "dolphins", "shark", "sharks",
      "seal", "seals", "barracuda", "jacks", "bumpheads", "cownose rays",
      "manta rays", "stingray", "stingrays", "waves", "tahiti"),
     ("海洋", "海底", "海岸", "海滩", "海浪", "海湾", "大海", "珊瑚",
      "水母", "海藻", "巨藻", "鲸", "海豚", "海豹", "海星", "鲨", "鲼",
      "梭鱼", "鲹鱼", "浪人鲹", "牛鼻鲼")),
    ("ice",
     ("ice", "iceberg", "icebergs", "glacier", "glaciers", "snow", "snowy",
      "arctic", "antarctic", "antarctica", "greenland", "frozen"),
     ("冰雪", "冰川", "冰山", "冰原", "雪山", "雪景", "雪地", "南极", "北极", "格陵兰")),
    ("desert",
     ("desert", "dune", "dunes", "sahara", "liwa", "namib", "gobi"),
     ("沙漠", "沙丘", "戈壁", "撒哈拉", "利瓦")),
    ("cities",
     ("city", "cities", "urban", "downtown", "skyline", "skyscraper",
      "skyscrapers", "hongkong", "hong kong", "new york", "golden gate",
      "san francisco", "london", "dubai", "shanghai", "tokyo"),
     ("城市", "都市", "市区", "天际线", "摩天楼", "香港", "纽约", "金门大桥",
      "旧金山", "伦敦", "迪拜", "上海", "东京")),
    ("mountains",
     ("mountain", "mountains", "mount", "alps", "alpine", "peak", "peaks",
      "valley", "valleys", "canyon", "canyons", "himalaya", "himalayas",
      "great wall"),
     ("山川", "山水", "山脉", "高山", "群山", "山峰", "峡谷", "山谷", "长城", "阿尔卑斯")),
    ("forest",
     ("forest", "forests", "rainforest", "rainforests", "woodland", "woods",
      "jungle", "trees", "bamboo"),
     ("森林", "雨林", "树林", "丛林", "竹林")),
)


def _contains_phrase(tokens, phrase):
    words = tuple(phrase.split())
    width = len(words)
    return any(tokens[i:i + width] == words
               for i in range(len(tokens) - width + 1))


def classify_video(path):
    """Return one stable scenery key (never ``all``) using basename evidence.

    Known numbered stock identities take priority over keyword heuristics, with
    optional 4K/1080/1080p suffixes. Unnumbered user names are NOT stock overrides.
    Case and separators are ignored; unknown/invalid paths return ``other``.
    Ambiguous generic names follow space/ocean/ice/desert/cities/mountains/forest
    precedence. Classification does not claim to inspect the actual video.
    """
    try:
        stem = ntpath.splitext(ntpath.basename(os.fsdecode(path)))[0].casefold()
    except (TypeError, ValueError, OSError):
        return "other"
    tokens = _english_tokens(stem)
    stock_tokens = tokens
    if stock_tokens[-2:] in (("4", "k"), ("1080", "p")):
        stock_tokens = stock_tokens[:-2]
    elif stock_tokens[-1:] == ("1080",):
        stock_tokens = stock_tokens[:-1]
    # Reject non-ASCII extra text as well, so e.g. 中国_China_18 is not an
    # accidental exact stock identity after extracting only English tokens.
    if not re.search(r"[^\W\d_a-z]", stem) and stock_tokens in _STOCK_CATEGORIES:
        return _STOCK_CATEGORIES[stock_tokens]
    for category, english, chinese in _KEYWORDS:
        if (any(_contains_phrase(tokens, word) for word in english)
                or any(word in stem for word in chinese)):
            return category
    return "other"


def category_counts(items):
    """Count every tuple once, including ``all`` and zero-valued categories."""
    counts = {key: 0 for key, _label in SCENERY_CATEGORIES}
    for _name, path, _thumbnail in items:
        counts["all"] += 1
        counts[classify_video(path)] += 1
    return counts


def filter_scenery(items, query="", category="all"):
    """Return original indexes satisfying existing library search AND scenery.

    Search semantics are exactly preferences.filter_library (display name and
    video basename, not folders/thumbnails). Order and input tuples are retained.
    Unknown/invalid category values safely default to ``all``. ``items`` follows
    the existing indexable library-sequence contract; it is never mutated.
    """
    indexes = filter_library(items, query)
    if not isinstance(category, str) or category not in CATEGORY_LABELS or category == "all":
        return indexes
    return [index for index in indexes if classify_video(items[index][1]) == category]
