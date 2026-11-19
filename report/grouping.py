from facts.measurements import Measurement, Rows


def group_by_plant(rows: Rows) -> tuple[tuple[str, list[Measurement]], ...]:
    groups: dict[str, list[Measurement]] = {}
    for _, image in rows:
        groups.setdefault(image.plant, []).append(image)
    return tuple(sorted(groups.items()))
