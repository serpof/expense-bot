import re
from dataclasses import dataclass

# Категории по умолчанию: при первом запуске бот кладёт их во вкладку «Категории».
# Дальше список правится прямо в таблице, описание подсказывает Claude, что куда относить.
DEFAULT_CATEGORIES: list[tuple[str, str]] = [
    ("Продукты", "супермаркеты, рынок, продукты домой"),
    ("Кафе", "кафе, рестораны, кофе, готовая еда и доставка"),
    ("Дети", "сад, школа, кружки, занятия, игрушки, детские вещи"),
    ("Жильё", "аренда, коммунальные платежи, интернет, связь, ремонт, товары для дома"),
    ("Транспорт", "топливо, такси, общественный транспорт, парковка, обслуживание машины"),
    ("Здоровье", "аптека, врачи, анализы, медицинская страховка"),
    ("Одежда", "одежда и обувь"),
    ("Питомец", "корм, ветеринар, груминг"),
    ("Развлечения", "кино, хобби, поездки, подписки"),
    ("Прочие", "всё, что не подходит к остальным"),
]
FALLBACK_CATEGORY = "Прочие"

# Стартовый словарь «слово → категория». Новые слова бот добавляет сам.
DEFAULT_ALIASES: dict[str, str] = {
    "сад": "Дети",
    "английский": "Дети",
    "булка": "Продукты",
    "кофе": "Кафе",
    "кафе": "Кафе",
    "такси": "Транспорт",
}

# Слова, по которым категорию не определить: предлоги, города и служебные слова из выписок.
STOPWORDS = {
    "на", "за", "из", "по", "от", "до", "для", "при", "под", "над", "про", "без",
    "doo", "ad", "pos", "novi", "sad", "beograd", "srbija", "rs", "www", "com",
    "rsd", "eur", "usd", "kartica", "kupovina", "placanje", "plaćanje", "transakcija",
}

_WORD = re.compile(r"[^\W\d_]+")  # последовательность букв любого алфавита


@dataclass(frozen=True)
class CategoryResult:
    category: str
    keyword: str | None  # слово, по которому выбрана категория
    source: str  # "dictionary" | "claude" | "fallback"


def normalize_word(word: str) -> str:
    return word.strip().lower().replace("ё", "е")


def words(text: str | None) -> list[str]:
    """Значимые слова текста в нижнем регистре: без цифр, знаков и стоп-слов."""
    if not text:
        return []
    found = (normalize_word(w) for w in _WORD.findall(text))
    return [w for w in found if len(w) >= 2 and w not in STOPWORDS]


def match_category(name: str, categories: list[str]) -> str | None:
    """Имя категории из списка без учёта регистра: «кафе» → «Кафе»."""
    wanted = name.strip().lower()
    return next((c for c in categories if c.lower() == wanted), None)


def find_in_dictionary(text: str | None, aliases: dict[str, str]) -> tuple[str, str] | None:
    """Первое слово текста, которое есть в словаре → (категория, слово)."""
    for word in words(text):
        if word in aliases:
            return aliases[word], word
    return None


def fallback_category(categories: list[str]) -> str:
    return match_category(FALLBACK_CATEGORY, categories) or categories[-1]


def learnable_keyword(keyword: str | None, text: str | None) -> str | None:
    """Слово, которое можно запомнить в словаре: ровно одно значимое слово из этого текста."""
    candidates = words(keyword)
    if len(candidates) != 1:
        return None
    return candidates[0] if candidates[0] in words(text) else None
