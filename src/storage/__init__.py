"""Read-only market-data boundary for the personal QQQ Thermometer."""

from .market_data import (
    DEFAULT_PRICE_FIELD_MAPPING,
    SUPPORTED_SYMBOLS,
    DataContractError,
    DuplicateSnapshotError,
    JsonDataSourceAdapter,
    MarketDataRequest,
    PriceFieldMapping,
    RawSnapshot,
    RawSnapshotManifest,
    SourceResponse,
    map_price_record,
)
from .normalization import (
    DEFAULT_LISTING_DATES,
    ListingRegistry,
    NormalizedBar,
    NormalizationConfig,
    NormalizationResult,
    QualityEvent,
    QUALITY_STATUSES,
    TradingCalendar,
    normalize_snapshots,
)
from .indicators import (
    INDICATOR_DEFINITIONS,
    INDICATOR_NAMES,
    INDICATOR_VERSION,
    IndicatorDefinition,
    IndicatorRun,
    IndicatorSnapshot,
    calculate_indicator_snapshots,
)
from .sqlite_store import (
    MIGRATIONS,
    STORAGE_IMPLEMENTATION_VERSION,
    STORAGE_SCHEMA,
    STORAGE_SCHEMA_VERSION,
    Migration,
    SQLiteRepository,
    SQLiteStore,
    StorageConflictError,
    StorageError,
    StorageImmutableError,
    StorageSchemaError,
    StorageValidationError,
    StoredRecord,
)
_PAPER_EXPORTS = {
    "PAPER_EXECUTION_IMPLEMENTATION_VERSION",
    "PAPER_LEDGER_EVENT_SCHEMA",
    "PAPER_PORTFOLIO_SCHEMA",
    "PAPER_RECONCILIATION_SCHEMA",
    "PAPER_STATUS",
    "PaperDayInput",
    "PaperDayResult",
    "PaperExecutionConfig",
    "PaperInputError",
    "PaperPortfolioError",
    "PaperPortfolioService",
    "PaperPortfolioState",
    "PaperPrice",
    "PaperReconciliation",
    "PaperReconciliationError",
}


def __getattr__(name):
    """Load M09 only when requested, avoiding a domain/storage import cycle."""

    if name in _PAPER_EXPORTS:
        from importlib import import_module

        module = import_module(".paper_portfolio", __name__)
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "DEFAULT_PRICE_FIELD_MAPPING",
    "SUPPORTED_SYMBOLS",
    "DataContractError",
    "DuplicateSnapshotError",
    "JsonDataSourceAdapter",
    "MarketDataRequest",
    "PriceFieldMapping",
    "RawSnapshot",
    "RawSnapshotManifest",
    "SourceResponse",
    "map_price_record",
    "DEFAULT_LISTING_DATES",
    "ListingRegistry",
    "NormalizedBar",
    "NormalizationConfig",
    "NormalizationResult",
    "QualityEvent",
    "QUALITY_STATUSES",
    "TradingCalendar",
    "normalize_snapshots",
    "INDICATOR_DEFINITIONS",
    "INDICATOR_NAMES",
    "INDICATOR_VERSION",
    "IndicatorDefinition",
    "IndicatorRun",
    "IndicatorSnapshot",
    "calculate_indicator_snapshots",
    "MIGRATIONS",
    "STORAGE_IMPLEMENTATION_VERSION",
    "STORAGE_SCHEMA",
    "STORAGE_SCHEMA_VERSION",
    "Migration",
    "SQLiteRepository",
    "SQLiteStore",
    "StorageConflictError",
    "StorageError",
    "StorageImmutableError",
    "StorageSchemaError",
    "StorageValidationError",
    "StoredRecord",
    "PAPER_EXECUTION_IMPLEMENTATION_VERSION",
    "PAPER_LEDGER_EVENT_SCHEMA",
    "PAPER_PORTFOLIO_SCHEMA",
    "PAPER_RECONCILIATION_SCHEMA",
    "PAPER_STATUS",
    "PaperDayInput",
    "PaperDayResult",
    "PaperExecutionConfig",
    "PaperInputError",
    "PaperPortfolioError",
    "PaperPortfolioService",
    "PaperPortfolioState",
    "PaperPrice",
    "PaperReconciliation",
    "PaperReconciliationError",
]
