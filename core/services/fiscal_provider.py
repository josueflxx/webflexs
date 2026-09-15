"""Provider-neutral read contracts for fiscal services."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Tuple


class TaxpayerLookupStatus(str, Enum):
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    INACTIVE = "INACTIVE"
    PARTIAL = "PARTIAL"


@dataclass(frozen=True)
class TaxpayerLookupResult:
    """Normalized taxpayer data; never a direct ORM update payload."""

    status: TaxpayerLookupStatus
    cuit: str
    person_type: str = ""
    key_status: str = ""
    display_name: str = ""
    first_name: str = ""
    last_name: str = ""
    business_name: str = ""
    fiscal_address: Mapping[str, Any] = field(default_factory=dict)
    general_data: Mapping[str, Any] = field(default_factory=dict)
    general_regime: Optional[Mapping[str, Any]] = None
    taxes: Tuple[Mapping[str, Any], ...] = ()
    activities: Tuple[Mapping[str, Any], ...] = ()
    monotributo: Optional[Mapping[str, Any]] = None
    characterizations: Tuple[Mapping[str, Any], ...] = ()
    warnings: Tuple[str, ...] = ()
    source: str = ""
    raw_metadata: Mapping[str, Any] = field(default_factory=dict)
    is_partial: bool = False

    def as_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


class TaxpayerRegistryProvider(ABC):
    @abstractmethod
    def lookup_taxpayer(self, cuit: str) -> TaxpayerLookupResult:
        """Return normalized official data without mutating a client."""


class FiscalVoucherProvider(ABC):
    """Voucher contract restricted to reads during the pre-emission stage."""

    @abstractmethod
    def get_server_status(self) -> Mapping[str, Any]:
        pass

    @abstractmethod
    def list_points_of_sale(self) -> Mapping[str, Any]:
        pass

    @abstractmethod
    def get_last_voucher(self, *, doc_type: str) -> int:
        pass

    @abstractmethod
    def get_voucher(self, *, fiscal_document) -> Any:
        pass


# Compatibility name that makes the current no-emission scope explicit.
FiscalVoucherReadProvider = FiscalVoucherProvider
