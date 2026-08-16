"""All 64 tables and 8 `*_live` views from ../penny-pulse-migrations/0*.sql.

Grouped by the migration that creates them, not by phase, so that a column
question has exactly one place to go: the file named in each class docstring.

Two conventions run through every module:

- No `ForeignKey` and no `relationship()`. Half the FKs point at `auth.users`,
  which lives in another schema and is not in this MetaData, and `to_metadata()`
  would copy the rest into `live_metadata` where their targets do not exist. The
  database holds all 156 foreign keys; repositories join explicitly.
- `server_default` appears only where omitting it would change what SQLAlchemy
  emits: primary keys the database generates, and GENERATED columns, which are
  mapped with `Computed(...)` so they stay out of every INSERT and UPDATE.

The one place a model reads stricter than the SQL: the seven GENERATED columns
are typed non-optional even though Postgres marks a generated column nullable
unless told otherwise. Every input to all seven is NOT NULL, so none can produce
NULL, and typing them `| None` would cost every caller a check for a case that
cannot happen. `notification_ledger.local_week` is the exception that proves it —
it is the only one the migration declares NOT NULL outright.
"""

from app.models.ai_capture_social import (
    ChatMessage,
    Cohort,
    CohortMember,
    Household,
    HouseholdMember,
    IngestSource,
    LlmUsageDay,
    PendingTxn,
    QueryIntent,
    RawSignal,
)
from app.models.analytics import (
    BalanceAnchor,
    BudgetRecommendation,
    CategorySuggestionEvent,
    DailyClose,
    IncomeEvent,
    MerchantRule,
    RebalanceLine,
    RebalanceProposal,
    Recurring,
    RecurringCandidate,
    RecurringLive,
    RecurringOccurrence,
    StatBaseline,
    TxnAnomaly,
)
from app.models.base import Base, live_metadata
from app.models.core import (
    Account,
    AccountLive,
    BudgetLimit,
    BudgetLimitLive,
    BudgetPeriod,
    BudgetPeriodLive,
    Category,
    CategoryLive,
    Txn,
    TxnLive,
    TxnTag,
)
from app.models.goals import (
    Attachment,
    ChitFund,
    ChitInstalment,
    EmiInstalment,
    EmiSchedule,
    Goal,
    GoalContribution,
    GoalLive,
    RefundMatch,
    RefundWatch,
    RegretScore,
    ReviewSession,
    SinkingFund,
    WishlistItem,
)
from app.models.habit import (
    AuditLog,
    DataDeletionToken,
    DataJob,
    HabitLog,
    PushSubscription,
    Streak,
)
from app.models.insight import (
    Insight,
    InsightEvidence,
    InsightFeedback,
    InsightReadiness,
    InsightRule,
    NotificationLedger,
)
from app.models.profile import Profile, ProfileLive
from app.models.reference import (
    AnalysisBlock,
    CategoryTemplate,
    ColourSwatch,
    Currency,
    FeatureFlag,
    FeatureFlagDefinition,
    IconAsset,
    IconPack,
    SynonymGroup,
)

__all__ = [
    "Base",
    "live_metadata",
    # 0001 — reference
    "Currency",
    "CategoryTemplate",
    "AnalysisBlock",
    "SynonymGroup",
    "FeatureFlagDefinition",
    "FeatureFlag",
    # 0011 — icon catalog
    "IconPack",
    "IconAsset",
    "ColourSwatch",
    # 0002 — core money
    "Profile",
    "Account",
    "Category",
    "BudgetPeriod",
    "BudgetLimit",
    "Txn",
    "TxnTag",
    # 0003 — habit and ops
    "HabitLog",
    "Streak",
    "PushSubscription",
    "AuditLog",
    "DataDeletionToken",
    "DataJob",
    # 0004 — insights and notifications
    "InsightRule",
    "Insight",
    "InsightEvidence",
    "InsightFeedback",
    "InsightReadiness",
    "NotificationLedger",
    # 0005 — phase 2 truth and analytics
    "BalanceAnchor",
    "Recurring",
    "RecurringCandidate",
    "RecurringOccurrence",
    "MerchantRule",
    "IncomeEvent",
    "DailyClose",
    "StatBaseline",
    "TxnAnomaly",
    "BudgetRecommendation",
    "CategorySuggestionEvent",
    "RebalanceProposal",
    "RebalanceLine",
    # 0006 — phase 3 goals and debt
    "Attachment",
    "Goal",
    "GoalContribution",
    "WishlistItem",
    "SinkingFund",
    "EmiSchedule",
    "EmiInstalment",
    "ChitFund",
    "ChitInstalment",
    "RegretScore",
    "ReviewSession",
    "RefundWatch",
    "RefundMatch",
    # 0007 — phase 4-6 ai, capture, social
    "QueryIntent",
    "ChatMessage",
    "LlmUsageDay",
    "RawSignal",
    "IngestSource",
    "PendingTxn",
    "Household",
    "HouseholdMember",
    "Cohort",
    "CohortMember",
    # 0008 — soft-delete read views
    "ProfileLive",
    "AccountLive",
    "CategoryLive",
    "BudgetPeriodLive",
    "BudgetLimitLive",
    "TxnLive",
    "RecurringLive",
    "GoalLive",
]
