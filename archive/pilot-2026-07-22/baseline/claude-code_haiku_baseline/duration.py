def parse_duration(text: str) -> int:
    text = text.strip()

    if not text:
        raise ValueError("Empty duration string")

    total_seconds = 0
    i = 0
    seen_units = set()
    unit_order = {'d': 0, 'h': 1, 'm': 2, 's': 3}
    last_unit_order = -1

    while i < len(text):
        if text[i].isspace():
            raise ValueError("Whitespace inside duration string")

        if not text[i].isdigit():
            raise ValueError(f"Expected digit, got '{text[i]}'")

        num_start = i
        while i < len(text) and text[i].isdigit():
            i += 1

        number_str = text[num_start:i]
        number = int(number_str)

        if i >= len(text):
            raise ValueError("Number without unit")

        unit = text[i]
        i += 1

        if unit not in unit_order:
            raise ValueError(f"Unknown or invalid unit: '{unit}'")

        if unit in seen_units:
            raise ValueError(f"Duplicate unit: '{unit}'")

        current_unit_order = unit_order[unit]
        if current_unit_order <= last_unit_order:
            raise ValueError("Units out of order")

        seen_units.add(unit)
        last_unit_order = current_unit_order

        unit_seconds = {'d': 86400, 'h': 3600, 'm': 60, 's': 1}
        total_seconds += number * unit_seconds[unit]

    return total_seconds


def format_duration(seconds: int) -> str:
    if seconds < 0:
        raise ValueError("Negative duration")

    if seconds == 0:
        return "0s"

    units = [
        ('d', 86400),
        ('h', 3600),
        ('m', 60),
        ('s', 1),
    ]

    result = []
    remaining = seconds

    for unit_name, unit_seconds in units:
        if remaining >= unit_seconds:
            count = remaining // unit_seconds
            remaining = remaining % unit_seconds
            result.append(f"{count}{unit_name}")

    return ''.join(result)
