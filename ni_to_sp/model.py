from dataclasses import dataclass


def sp_pad_number(index):
    return (3 - index % 16 // 4) * 4 + index % 4 + 1


def role_names(size=16):
    if size != 16:
        raise ValueError("NI kits contain 16 pads.")
    return tuple(f"Maschine pad {i + 1}" for i in range(16))


def is_sp_target(target):
    return target == "sp404"


def kit_count(session):
    return 1


@dataclass
class Sample:
    id: str
    path: str
    fingerprint: str
    kind: str = "captured"
    features: dict | None = None

    def __post_init__(self):
        self.features = self.features or {"available": True}


@dataclass
class Assignment:
    sample_id: str | None = None


@dataclass
class Session:
    samples: list
    pads: list
    kit_size: int = 16
    target: str = "sp404"
    seed: int = 1000
    library_revision: int = 0

    @property
    def by_id(self):
        return {sample.id: sample for sample in self.samples}
