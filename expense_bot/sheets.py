def append_expense(worksheet, expense: dict, author: str, message_id: int, date: str) -> None:
    row = [date, float(expense["amount"]), expense["currency"], "", expense["comment"] or "", author, message_id]
    worksheet.append_row(row, value_input_option="USER_ENTERED")