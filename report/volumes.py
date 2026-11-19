from collections.abc import Sequence
from dataclasses import dataclass

from facts.measurements import Measurement, Rows


@dataclass(frozen=True)
class PlantTotal:
    plant: str
    count: int
    liters: int


def total_plant(plant: str, images: Sequence[Measurement]) -> PlantTotal:
    return PlantTotal(plant, len(images), sum(image.liters for image in images))


def total_liters(rows: Rows) -> int:
    return sum(image.liters for _, image in rows)
