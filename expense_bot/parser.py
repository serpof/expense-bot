from decimal import Decimal, InvalidOperation

# Слово или символ валюты → код ISO. Ищем в нижнем регистре.
CURRENCIES = {
    "динар": "RSD", "динара": "RSD", "динаров": "RSD", "дин": "RSD", "rsd": "RSD", "din": "RSD",
    "рубль": "RUB", "рубля": "RUB", "рублей": "RUB", "руб": "RUB", "rub": "RUB", "₽": "RUB",
    "евро": "EUR", "eur": "EUR", "euro": "EUR", "€": "EUR",
    "доллар": "USD", "доллара": "USD", "долларов": "USD", "usd": "USD", "$": "USD",
    "фунт": "GBP", "фунта": "GBP", "фунтов": "GBP", "gbp": "GBP", "£": "GBP",
}

# Символы, которые пишут слитно с суммой: «450€», «€450».
SYMBOLS = {"€": "EUR", "$": "USD", "₽": "RUB", "£": "GBP"}


def _split_symbol(word: str) -> tuple[str, str | None]:
    """«450€» → ("450", "EUR"). Если символа нет — слово без изменений."""
    for symbol, code in SYMBOLS.items():
        if len(word) > 1 and word.endswith(symbol):
            return word[:-1], code
        if len(word) > 1 and word.startswith(symbol):
            return word[1:], code
    return word, None


def _looks_like_number(word: str) -> bool:
    return word.replace(",", "").replace(".", "").isdigit()


def normalize_number(raw: str) -> str | None:
    """Строку с числом приводит к виду, который понимает Decimal.

    Запятая — дробная часть: «157,56». Точка перед группой из трёх цифр —
    разделитель тысяч: «1.200», «1.200,50». Иначе точка — дробная часть: «4.5».
    """
    if raw.count(",") > 1:
        return None
    integer, _, fraction = raw.partition(",")
    groups = integer.split(".")
    if "," in raw or all(len(g) == 3 for g in groups[1:]):
        integer = "".join(groups)
    elif len(groups) != 2:
        return None
    else:
        integer, fraction = groups
    return f"{integer}.{fraction}" if fraction else integer


def parse_expense(text: str) -> dict:
    words = text.split()
    currency = "RSD"
    amount_parts = []
    comment = []
    prev_was_number = False
    number_groups = 0
    for word in words:
        word, symbol_currency = _split_symbol(word)
        if symbol_currency:
            currency = symbol_currency
        if _looks_like_number(word):
            amount_parts.append(word)
            if not prev_was_number:
                number_groups += 1
                prev_was_number = True
        elif word.lower() in CURRENCIES:
            currency = CURRENCIES[word.lower()]
            prev_was_number = False
        else:
            comment.append(word.lower())
            prev_was_number = False

    amount = None
    if amount_parts and number_groups == 1:
        normalized = normalize_number("".join(amount_parts))
        try:
            amount = Decimal(normalized) if normalized else None
        except InvalidOperation:
            amount = None
    comment = " ".join(comment) if comment else None
    return {"amount": amount, "currency": currency, "comment": comment}
