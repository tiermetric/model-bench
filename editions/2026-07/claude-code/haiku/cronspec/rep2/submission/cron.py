from datetime import datetime, timedelta


def next_fire(expr: str, after: datetime) -> datetime:
    """
    Return the earliest datetime STRICTLY AFTER `after` that matches the cron
    expression `expr`. All times are UTC, tz-naive. The result has second == 0
    and microsecond == 0. `after` may have nonzero seconds/microseconds; the
    first candidate considered is the next whole minute strictly after `after`.
    """
    parser = CronParser(expr)
    fields = parser.parse()

    # Start from the next whole minute strictly after `after`
    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)

    # Limit search to avoid infinite loops (10 years forward)
    limit = after + timedelta(days=10*365 + 10*365//4)

    while candidate <= limit:
        minute, hour, day, month, dow = candidate.minute, candidate.hour, candidate.day, candidate.month, candidate.weekday()
        dow = (dow + 1) % 7  # Convert Monday=0 to Sunday=0

        if match_datetime(candidate, fields):
            return candidate

        candidate += timedelta(minutes=1)

    raise ValueError("No matching datetime found within search limit")


def match_datetime(dt: datetime, fields: dict) -> bool:
    """Check if a datetime matches all cron fields."""
    minute, hour, day, month, dow = dt.minute, dt.hour, dt.day, dt.month, (dt.weekday() + 1) % 7

    # Check minute, hour, month
    if minute not in fields['minute']:
        return False
    if hour not in fields['hour']:
        return False
    if month not in fields['month']:
        return False

    # Deviation 1: day-of-month AND day-of-week intersection logic
    dom_restricted = fields['dom_restricted']
    dow_restricted = fields['dow_restricted']

    if dom_restricted and dow_restricted:
        # Both restricted: must match BOTH
        dom_match = (day in fields['day']) or (fields['day_is_L'] and is_last_day_of_month(dt))
        dow_match = dow in fields['dow']
        return dom_match and dow_match
    elif dom_restricted:
        # Only day-of-month restricted
        return (day in fields['day']) or (fields['day_is_L'] and is_last_day_of_month(dt))
    elif dow_restricted:
        # Only day-of-week restricted
        return dow in fields['dow']
    else:
        # Both unrestricted
        return True


def is_last_day_of_month(dt: datetime) -> bool:
    """Check if dt is the last day of its month."""
    month, year = dt.month, dt.year
    if month == 12:
        last_day = 31
    elif month in [1, 3, 5, 7, 8, 10]:
        last_day = 31
    elif month in [4, 6, 9, 11]:
        last_day = 30
    else:  # February
        if is_leap_year(year):
            last_day = 29
        else:
            last_day = 28
    return dt.day == last_day


def is_leap_year(year: int) -> bool:
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)


class CronParser:
    """Parse and validate a cron expression."""

    MONTH_NAMES = {
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
        'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
    }

    DOW_NAMES = {
        'sun': 0, 'mon': 1, 'tue': 2, 'wed': 3, 'thu': 4, 'fri': 5, 'sat': 6
    }

    DOMAINS = {
        0: (0, 59),      # minute
        1: (0, 23),      # hour
        2: (1, 31),      # day-of-month
        3: (1, 12),      # month
        4: (0, 6),       # day-of-week
    }

    def __init__(self, expr: str):
        self.expr = expr.strip()

    def parse(self) -> dict:
        """Parse the cron expression and return a dict with parsed fields."""
        parts = self.expr.split()
        if len(parts) != 5:
            raise ValueError(f"Expected 5 fields, got {len(parts)}")

        fields = {}
        dom_is_L = False

        # Parse minute, hour, month, dow
        fields['minute'] = self._parse_field(parts[0], 0)
        fields['hour'] = self._parse_field(parts[1], 1)

        # Parse day-of-month (special case for L)
        day_field, dom_is_L = self._parse_day_of_month(parts[2])
        fields['day'] = day_field
        fields['day_is_L'] = dom_is_L

        fields['month'] = self._parse_field(parts[3], 3)
        fields['dow'] = self._parse_field(parts[4], 4)

        # Track which fields are restricted (not bare *)
        fields['dom_restricted'] = dom_is_L or parts[2] != '*'
        fields['dow_restricted'] = parts[4] != '*'

        return fields

    def _parse_day_of_month(self, field_str: str) -> tuple:
        """Parse day-of-month field, which can be L."""
        if field_str == 'L':
            return (set(), True)  # Return empty set and True flag for L
        elif 'L' in field_str:
            raise ValueError("L may only be the entire day-of-month field")
        else:
            values = self._parse_field(field_str, 2)
            return (values, False)

    def _parse_field(self, field_str: str, field_index: int) -> set:
        """Parse a single cron field and return a set of matching values."""
        lo, hi = self.DOMAINS[field_index]

        if field_str == '*':
            return set(range(lo, hi + 1))

        values = set()
        for part in field_str.split(','):
            part = part.strip()
            if not part:
                raise ValueError("Empty list element")

            # Check for step
            if '/' in part:
                range_part, step_part = part.split('/', 1)
                try:
                    step = int(step_part)
                except ValueError:
                    raise ValueError(f"Invalid step value: {step_part}")

                if step <= 0:
                    raise ValueError(f"Step must be positive, got {step}")

                if range_part == '*':
                    # Deviation 2: anchor to domain floor
                    current = lo
                    while current <= hi:
                        values.add(current)
                        current += step
                elif '-' in range_part:
                    # Step on a range
                    start, end = self._extract_range_bounds(range_part, field_index)
                    # Deviation 2: anchor to range start and count through wrap
                    if start <= end:
                        # Non-wrapped range
                        current = start
                        while current <= end:
                            values.add(current)
                            current += step
                    else:
                        # Wrapped range: create forward list starting at start
                        wrapped_list = []
                        current = start
                        while True:
                            wrapped_list.append(current)
                            if current == end:
                                break
                            current += 1
                            if current > hi:
                                current = lo
                        # Take every step-th element starting from index 0
                        for i, val in enumerate(wrapped_list):
                            if i % step == 0:
                                values.add(val)
                else:
                    # Step on a single value (invalid)
                    raise ValueError("Step may only be applied to * or a range")
            elif '-' in part:
                # Range without step
                values.update(self._parse_range(part, field_index))
            else:
                # Single value
                val = self._parse_value(part, field_index)
                values.add(val)

        return values

    def _parse_value(self, value_str: str, field_index: int) -> int:
        """Parse a single value (number or name)."""
        lo, hi = self.DOMAINS[field_index]

        # Try month name
        if field_index == 3:
            if value_str in self.MONTH_NAMES:
                val = self.MONTH_NAMES[value_str]
                if lo <= val <= hi:
                    return val
                raise ValueError(f"Month value {val} out of domain [{lo}, {hi}]")

        # Try day-of-week name
        if field_index == 4:
            if value_str in self.DOW_NAMES:
                val = self.DOW_NAMES[value_str]
                if lo <= val <= hi:
                    return val
                raise ValueError(f"Day-of-week value {val} out of domain [{lo}, {hi}]")

        # Must be numeric
        try:
            val = int(value_str)
        except ValueError:
            raise ValueError(f"Invalid value: {value_str}")

        if not (lo <= val <= hi):
            raise ValueError(f"Value {val} out of domain [{lo}, {hi}]")

        return val

    def _parse_range(self, range_str: str, field_index: int) -> set:
        """Parse a range A-B and return matching values (including wrapping)."""
        lo, hi = self.DOMAINS[field_index]
        parts = range_str.split('-')

        if len(parts) != 2:
            raise ValueError(f"Invalid range: {range_str}")

        start = self._parse_value(parts[0], field_index)
        end = self._parse_value(parts[1], field_index)

        if start <= end:
            return set(range(start, end + 1))
        else:
            # Rule 4: wrap around
            return set(range(start, hi + 1)) | set(range(lo, end + 1))

    def _extract_range_bounds(self, range_str: str, field_index: int) -> tuple:
        """Extract start and end of a range for step calculation."""
        parts = range_str.split('-')
        start = self._parse_value(parts[0], field_index)
        end = self._parse_value(parts[1], field_index)
        return (start, end)
