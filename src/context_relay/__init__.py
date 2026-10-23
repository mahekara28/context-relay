"""Context Relay: explicit, evidence-linked state checks for agent harnesses."""

from .engine import Relay
from .adapters import guarded_tool_call
from .models import BudgetExceeded, ConflictError, PolicyError, ToolExecutionError
from .store import RelayStore, StoreBusy
from .context import ContextPlan, compile_context
from .budget import TokenBudget, TokenBudgetExceeded, make_hf_token_counter
from .evidence import EvidenceArchive, EvidenceMismatchError, RecoveredEvidence
from .middleware import ActionRejected, ContextGuard, GuardedTurn, PreparedTurn
from .config import RelayConfig
from .connection import AgentResult, AsyncConnectedAgent, ConnectedAgent, CorrectionRequired, MemoryClarificationRequired, connect
from .corrections import CorrectionField, CorrectionReview, review_corrections
from .grounding import EvidenceCatalog, EvidenceCheck
from .protocols import compile_ollama_context
from .recovery import GroundedResult, UnsupportedResponseError
from .memory import AutomaticMemory, MemoryPolicy, MemoryHistoryError, MemoryBudgetExceeded, MemoryConflictError
from .attachment import RelayAttachment, attach
from .openai import connect_openai, compile_openai_chat_context
from .anthropic import connect_anthropic
from .native import NativeConnection, AsyncNativeConnection, NativePrepared, NativeResult
from .output import OutputPolicy, OutputReport, OutputRejected
from .content import ContentPolicy, ContentCheck, RowRule
from .verification import VerificationPolicy, VerificationCheck, VerificationUnavailable
from .outcomes import OutcomePolicy, OutcomeVerifier
from .fresh_evidence import EvidenceSource, EvidenceSnapshot, EvidenceChanged
from .responses_workflow import ResponsesWorkflow, WorkflowResult, WorkflowPending, WorkflowLimitExceeded
from .tools import ToolRegistry, BoundTools
from .coverage import CoveragePolicy, CoverageError
from .workflow_history import WorkflowHistoryPolicy
from .actions import ActionHarness, ActionResult, ActionProposalError
from .provider_actions import ActionDecoder, NativeActionHarness, provider_capabilities
from .interpretation import MemoryInterpreter, MemoryInterpretationError
from .automatic import auto_connect
from .persistence import named_storage_path
from .storage_privacy import HistoryRetention
from .organization import Principal, SessionManager, AccessDenied, CapacityExceeded
from .pilot import run_pilot
from .harness_eval import (HarnessCase, HarnessBudget, HarnessBudgetExceeded,
                           evaluate_harness, assess_improvement, improve_harness)
from .langchain import connect_langchain, LangChainRelay, LangChainPrepared, LangChainResponseError
from .conversation import (Conversation, AsyncConversation, ConversationPending, ConversationBusy,
                           ConversationAlreadyCompleted, ConversationLimitExceeded, ConversationResponseError,
                           ConversationArchived, HistoryPolicy, TaskContextLimitExceeded)

__version__ = "0.22.0a2"
__all__ = ["Relay", "RelayStore", "StoreBusy", "guarded_tool_call", "ContextGuard", "GuardedTurn", "ActionRejected",
           "ContextPlan", "compile_context", "PreparedTurn",
           "TokenBudget", "TokenBudgetExceeded", "make_hf_token_counter",
           "EvidenceArchive", "EvidenceMismatchError", "RecoveredEvidence",
           "RelayConfig", "AgentResult", "ConnectedAgent", "AsyncConnectedAgent", "connect",
           "CorrectionField", "CorrectionReview", "CorrectionRequired", "MemoryClarificationRequired", "review_corrections",
           "EvidenceCatalog", "EvidenceCheck", "GroundedResult", "UnsupportedResponseError", "compile_ollama_context",
           "AutomaticMemory", "MemoryPolicy", "MemoryHistoryError", "MemoryBudgetExceeded", "MemoryConflictError",
           "RelayAttachment", "attach", "auto_connect", "named_storage_path", "HistoryRetention",
           "Principal", "SessionManager", "AccessDenied", "CapacityExceeded", "run_pilot", "HistoryPolicy", "TaskContextLimitExceeded",
           "HarnessCase", "HarnessBudget", "HarnessBudgetExceeded",
           "evaluate_harness", "assess_improvement", "improve_harness",
           "connect_langchain", "LangChainRelay", "LangChainPrepared", "LangChainResponseError",
           "connect_openai", "compile_openai_chat_context", "Conversation", "AsyncConversation",
           "connect_anthropic", "OutputPolicy", "OutputReport", "OutputRejected",
           "ActionHarness", "ActionResult", "ActionProposalError",
           "ActionDecoder", "NativeActionHarness", "provider_capabilities",
           "VerificationPolicy", "VerificationCheck", "VerificationUnavailable", "OutcomePolicy", "OutcomeVerifier",
           "EvidenceSource", "EvidenceSnapshot", "EvidenceChanged",
           "ResponsesWorkflow", "WorkflowResult", "WorkflowPending", "WorkflowLimitExceeded", "WorkflowHistoryPolicy",
           "CoveragePolicy", "CoverageError",
           "ContentPolicy", "ContentCheck", "RowRule", "ToolRegistry", "BoundTools",
           "NativeConnection", "AsyncNativeConnection", "NativePrepared", "NativeResult",
           "MemoryInterpreter", "MemoryInterpretationError", "ConversationArchived",
           "ConversationPending", "ConversationBusy", "ConversationAlreadyCompleted",
           "ConversationLimitExceeded", "ConversationResponseError",
           "BudgetExceeded", "ConflictError", "PolicyError", "ToolExecutionError"]
