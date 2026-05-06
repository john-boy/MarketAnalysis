"""Extractor specification — one doc per ML-feed extractor in ``extractors``.

An extractor is a named, persisted recipe for building a single
DataFrame fed into an ML model.  Two kinds:

- **etf** — relative-strength analysis: SPY + the ETF + the ETF's
  current constituent stocks.  ``etf_symbol`` names the ETF; the
  resolver looks up its holdings at extract time.
- **rotation** — sector-rotation analysis: SPY + a user-supplied list
  of ETFs (e.g. the SPDR sector set).  ``symbols`` is the list.

Every extract spans dates from ``start_date`` forward.  ``SPY`` is
always present in the output (callers don't have to remember to add
it themselves).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from market_analysis.data.models._base import MongoModel, utcnow


ExtractorKind = Literal["etf", "rotation"]


class Extractor(MongoModel):
    name: str                            # unique user-facing identifier
    kind: ExtractorKind
    etf_symbol: str | None = None        # required when kind == "etf"
    symbols: list[str] = Field(default_factory=list)  # required when kind == "rotation"
    start_date: datetime                 # first bar date in the output series

    description: str | None = None
    last_run: datetime | None = None

    added_at: datetime = Field(default_factory=utcnow)
    last_updated: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _kind_payload(self):
        if self.kind == "etf":
            if not self.etf_symbol:
                raise ValueError("etf-kind extractor requires etf_symbol")
        elif self.kind == "rotation":
            if not self.symbols:
                raise ValueError("rotation-kind extractor requires non-empty symbols")
        return self
