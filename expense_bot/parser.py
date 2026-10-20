from decimal import Decimal


CURRENCIES = {"динар": "RSD", "динара": "RSD", "рубль": "RUB", "рубля": "RUB",
              "рублей": "RUB", "руб": "RUB", "евро": "EUR", "доллар": "USD", "доллара": "USD", "долларов": "USD"}

def parse_expense(text: str) -> dict:
    words = text.split()
    currency = "RSD"
    amount_part = []
    comment = []
    prev_was_number = False
    number_groups = 0
    for word in words:
        if word.replace(",", "").isdigit():
            amount_part.append(word.replace(",", "."))
            if not prev_was_number:
                number_groups += 1
                prev_was_number = True
        elif word.lower() in CURRENCIES:
            currency = CURRENCIES[word.lower()]
            prev_was_number = False
        else:
            comment.append(word.lower())
            prev_was_number = False

    amount = Decimal("".join(amount_part)) if amount_part and number_groups == 1 else None
    comment = " ".join(comment) if comment else None
    return {"amount": amount, "currency": currency, "comment": comment}