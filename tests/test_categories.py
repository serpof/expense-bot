import pytest

from expense_bot.categories import (
    fallback_category,
    find_in_dictionary,
    learnable_keyword,
    match_category,
    words,
)

ALIASES = {"кофе": "Кафе", "сад": "Дети", "maxi": "Продукты", "елка": "Прочие"}


@pytest.mark.parametrize("text, expected", [
    ("кофе латте", ["кофе", "латте"]),
    ("Кофе, латте!", ["кофе", "латте"]),
    ("ужин на двоих", ["ужин", "двоих"]),  # предлоги не в счёт
    ("MAXI 123 NOVI SAD", ["maxi"]),  # город и цифры из выписки не в счёт
    ("Ёлка", ["елка"]),
    (None, []),
    ("", []),
])
def test_words(text, expected):
    assert words(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("кофе латте", ("Кафе", "кофе")),
    ("латте и кофе", ("Кафе", "кофе")),  # знакомое слово не первым
    ("сад и кофе", ("Дети", "сад")),  # два знакомых слова — побеждает первое
    ("MAXI 0123 NOVI SAD", ("Продукты", "maxi")),
    ("новогодняя ёлка", ("Прочие", "елка")),
    ("шаурма", None),
    (None, None),
])
def test_find_in_dictionary(text, expected):
    assert find_in_dictionary(text, ALIASES) == expected


def test_match_category_ignores_case():
    assert match_category("кафе", ["Продукты", "Кафе"]) == "Кафе"
    assert match_category("Такси", ["Продукты", "Кафе"]) is None


def test_fallback_category():
    assert fallback_category(["Кафе", "Прочие", "Дети"]) == "Прочие"
    assert fallback_category(["Кафе", "Другое"]) == "Другое"  # «Прочие» переименовали


@pytest.mark.parametrize("keyword, text, expected", [
    ("шаурма", "шаурма у метро", "шаурма"),
    ("Шаурма", "шаурма у метро", "шаурма"),
    ("бургер", "шаурма у метро", None),  # слова нет в тексте
    ("шаурма метро", "шаурма у метро", None),  # нужно ровно одно слово
    ("", "шаурма", None),
    (None, "шаурма", None),
])
def test_learnable_keyword(keyword, text, expected):
    assert learnable_keyword(keyword, text) == expected
